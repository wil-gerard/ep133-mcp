"""transcribe_groove against the synthesized fixture.

The fixture has no isolated kick or snare: every kick coincides with a hat and
every snare with a kick and a hat, so the kit extract_kit cuts from it has a
kick+hat slice on the kick pad and a kick+snare+hat slice on the snare pad.
The groove is therefore checked as *sounds per step*: the union of the fixture
sounds inside each pad's slice, at every step the transcription fires that pad,
must equal synth_reference.PATTERN. That is what "the kit plays the groove"
means for one-shots cut from a mix.

The fallback path fixes the downbeat explicitly (librosa's kick-energy guess is
ambiguous when a kick sits on every beat); the Beat This! case runs without any
hint and only when its checkpoint is already cached.
"""

import json
from pathlib import Path

import pytest

pytest.importorskip('numpy', reason='audio extra not installed')
pytest.importorskip('librosa', reason='audio extra not installed')

from ep133_mcp.audio import groove, kit
from ep133_mcp.audio.errors import InvalidReference
import synth_reference
from test_analysis import beat_this_cached

FALLBACK = {'separation': 'hpss', 'beat_tracker': 'librosa'}
STEP_TOLERANCE_S = 0.03


@pytest.fixture(scope='module')
def fixture(tmp_path_factory):
    truth = synth_reference.write(tmp_path_factory.mktemp('ref') / 'ref')
    kit.extract_kit(truth['clip'], **FALLBACK)
    return truth


def slice_sounds(truth, record) -> dict[str, set[str]]:
    """Which fixture sounds each drum pad's slice contains, from the generator's event list."""
    saved = json.loads(Path(record['kit']).read_text())
    return {str(s['pad']): {name for name, t in truth['events'] if abs(t - s['onset_s']) < STEP_TOLERANCE_S}
            for s in saved['slices'] if s['class'] in kit.DRUM_CLASSES}


def sounds_per_step(truth, record) -> list[set[str]]:
    contents = slice_sounds(truth, record)
    steps = record['pattern']['steps']
    total = record['bars'] * groove.STEPS_PER_BAR
    return [set().union(*(contents[pad] for pad, row in steps.items() if row[i] == 'x')) for i in range(total)]


def expected_per_step(bars: int) -> list[set[str]]:
    return [{name for name, row in synth_reference.PATTERN.items() if row[i % 16] == 'x'}
            for i in range(bars * groove.STEPS_PER_BAR)]


def test_fallback_groove_matches_pattern(fixture):
    record = groove.transcribe_groove(fixture['clip'], downbeat_s=0.0, **FALLBACK)
    assert record['status'] == 'transcribed' and record['probable'] is True
    assert record['bars'] == 2 and record['grid'] == 'beats'
    assert sounds_per_step(fixture, record) == expected_per_step(2)
    assert {p['class'] for p in record['pads']} == set(kit.DRUM_CLASSES)
    assert all(len(row) == 32 and set(row) <= {'x', '.'} for row in record['pattern']['steps'].values())
    assert record['pattern']['group'] == 'A' and record['pattern']['index'] == 1
    assert record['quantization']['mean_ms'] < 15 and record['quantization']['max_ms'] < 30
    assert record['quantization']['placed'] + record['quantization']['dropped_retriggers'] == record['quantization']['onsets']
    assert json.loads(Path(record['groove']).read_text())['pattern'] == record['pattern']


@pytest.mark.skipif(not beat_this_cached(), reason='Beat This! checkpoint not in the local torch cache')
def test_beat_this_groove_without_hints(fixture):
    record = groove.transcribe_groove(fixture['clip'], separation='hpss', beat_tracker='beat_this')
    assert record['downbeat_s'] < 0.05 and record['bpm'] == pytest.approx(120.0, abs=0.5)
    assert sounds_per_step(fixture, record) == expected_per_step(2)


def test_bar_count_folds_and_extends(fixture):
    one = groove.transcribe_groove(fixture['clip'], bars=1, downbeat_s=0.0, **FALLBACK)
    assert sounds_per_step(fixture, one) == expected_per_step(1)
    assert one['quantization']['folded'] > 0
    four = groove.transcribe_groove(fixture['clip'], bars=4, downbeat_s=0.0, group='B', index=3, **FALLBACK)
    assert sounds_per_step(fixture, four)[:32] == expected_per_step(2)
    assert all(not s for s in sounds_per_step(fixture, four)[33:])
    assert four['pattern']['group'] == 'B' and four['pattern']['index'] == 3


def test_wrong_downbeat_is_a_rotation(fixture):
    """The fallback tracker's guessed downbeat only rotates the groove by whole beats."""
    guessed = groove.transcribe_groove(fixture['clip'], **FALLBACK)
    phase = guessed['downbeat_s']
    shift = round(phase / (60 / guessed['bpm']) * groove.STEPS_PER_BEAT)
    assert shift % groove.STEPS_PER_BEAT == 0
    got = sounds_per_step(fixture, guessed)
    assert got == expected_per_step(2)[shift:] + expected_per_step(2)[:shift]


def test_choose_bars():
    assert [groove.choose_bars(b) for b in (0.6, 1.3, 1.5, 2.9, 3.0, 9.0)] == [1, 1, 2, 4, 4, 4]


def test_beat_grid_interpolates_and_extrapolates():
    grid = groove.BeatGrid([1.0, 1.5, 2.1], bpm=0.0, downbeat_s=1.5)
    assert grid.mode == 'beats' and grid.origin == pytest.approx(1.0)
    assert grid.position(1.8) == pytest.approx(1.5)
    assert grid.position(0.45) == pytest.approx(-1.0)
    assert grid.time(grid.position(2.65)) == pytest.approx(2.65)
    steps, error = grid.step(1.8)
    assert steps == pytest.approx(2.0) and abs(error) < 1e-9
    fixed = groove.BeatGrid([], bpm=120.0, downbeat_s=0.25)
    assert fixed.mode == 'bpm' and fixed.step(0.75)[0] == pytest.approx(4.0)


@pytest.mark.parametrize('kwargs', [{'bars': 3}, {'group': 'E'}, {'index': 0}])
def test_bad_arguments(fixture, kwargs):
    with pytest.raises(InvalidReference):
        groove.transcribe_groove(fixture['clip'], **{**FALLBACK, **kwargs})


def test_missing_kit(tmp_path):
    truth = synth_reference.write(tmp_path / 'ref')
    with pytest.raises(InvalidReference):
        groove.transcribe_groove(truth['clip'], **FALLBACK)


def test_kit_from_another_clip_is_rejected(fixture, tmp_path):
    """Exemplars are times into the kit's own clip: a foreign kit must not silently mis-transcribe."""
    record = json.loads(Path(groove.kit_paths(fixture['clip'])[1]).read_text())
    for entry in record['slices']:
        entry['source_s'] = [entry['source_s'][0] + 3600.0, entry['source_s'][1] + 3600.0]
    foreign = tmp_path / 'foreign_kit.json'
    foreign.write_text(json.dumps(record))
    with pytest.raises(InvalidReference, match='different clip'):
        groove.transcribe_groove(fixture['clip'], kit=foreign, downbeat_s=0.0, **FALLBACK)
