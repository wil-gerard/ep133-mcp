"""analyze_reference against the synthesized fixture: tempo, key, downbeat, onsets, cache.

No test downloads model weights. The fallback path (HPSS + librosa) always
runs; the Beat This! case runs only when its checkpoint is already in the
local torch cache, so CI without it skips rather than fetching 77 MB.
"""

import json
from pathlib import Path

import pytest

np = pytest.importorskip('numpy', reason='audio extra not installed')
pytest.importorskip('librosa', reason='audio extra not installed')

from ep133_mcp.audio import analysis
from ep133_mcp.audio.errors import InvalidReference
import synth_reference

BPM = 97.0
BARS = 4


def beat_this_cached() -> bool:
    try:
        import torch
    except ImportError:
        return False
    checkpoint = Path(torch.hub.get_dir()) / 'checkpoints' / f'beat_this-{analysis.BEAT_THIS_CHECKPOINT}.ckpt'
    return checkpoint.is_file()


@pytest.fixture
def truth(tmp_path):
    return synth_reference.write(tmp_path / 'ref', bpm=BPM, bars=BARS)


def test_fallback_roundtrip(truth):
    record = analysis.analyze_reference(truth['clip'], separation='hpss', beat_tracker='librosa')
    assert record['status'] == 'analyzed'
    assert record['separation']['method'] == 'hpss'
    assert record['beat_tracker'] == {'method': 'librosa', 'checkpoint': None}
    # librosa's tracker is the fallback; it is looser than the 1 % the pipeline wants.
    assert abs(record['bpm']['value'] - BPM) / BPM < 0.03
    assert record['bpm']['confidence'] > 0.5
    assert record['key']['name'] == 'A minor'
    assert record['downbeat']['probable'] and record['downbeat']['beats_per_bar'] == 4
    assert record['downbeat']['phase'] in range(4)                # kick on every beat: phase is a guess here
    assert record['stems']['vocals'] is None
    drums = record['stems']['drums']
    assert Path(drums['path']).is_file()
    assert len(drums['onsets_s']) >= 16                     # at least every kick in four bars
    assert len(drums['onsets_s']) == len(drums['strength']) == len(drums['starts_s'])
    assert all(0 < s <= 1 for s in drums['strength'])
    assert record['stems']['bass']['onsets_s']


@pytest.mark.skipif(not beat_this_cached(), reason='Beat This! checkpoint not in the local torch cache')
def test_beat_this_roundtrip(truth):
    record = analysis.analyze_reference(truth['clip'], separation='hpss', beat_tracker='beat_this')
    assert record['beat_tracker'] == {'method': 'beat_this', 'checkpoint': analysis.BEAT_THIS_CHECKPOINT}
    assert abs(record['bpm']['value'] - BPM) / BPM < 0.01
    beat = truth['step_s'] * 4
    assert all(min(abs(b - k * beat) for k in range(BARS * 4 + 1)) < 0.03 for b in record['bpm']['beats_s'])
    downbeat = record['downbeat']
    assert downbeat['probable'] and downbeat['downbeats_s']
    assert downbeat['value_s'] < 0.05
    assert downbeat['beats_per_bar'] in (2, 4)               # kick on every beat reads as either
    assert record['key']['name'] == 'A minor'


def test_cache_and_force(truth):
    first = analysis.analyze_reference(truth['clip'], separation='hpss', beat_tracker='librosa')
    cache, _ = analysis.analysis_paths(truth['clip'])
    assert json.loads(cache.read_text())['bpm'] == first['bpm']
    assert analysis.analyze_reference(truth['clip'])['status'] == 'cached'
    Path(first['stems']['drums']['path']).unlink()
    again = analysis.analyze_reference(truth['clip'], separation='hpss', beat_tracker='librosa')
    assert again['status'] == 'analyzed'
    assert Path(again['stems']['drums']['path']).is_file()


@pytest.mark.parametrize('kwargs', [{'separation': 'spleeter'}, {'beat_tracker': 'madmom'}])
def test_unknown_method_rejected(truth, kwargs):
    with pytest.raises(InvalidReference):
        analysis.analyze_reference(truth['clip'], **{'separation': 'hpss', 'beat_tracker': 'librosa', **kwargs})


def test_missing_clip(tmp_path):
    with pytest.raises(InvalidReference):
        analysis.analyze_reference(tmp_path / 'nope.wav')
