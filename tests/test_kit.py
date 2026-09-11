"""extract_kit against the synthesized fixture: classes, notes, format, bytes, layout.

Runs the fallback path only (HPSS + librosa); no model weights are downloaded.
"""

import json
from pathlib import Path

import pytest

np = pytest.importorskip('numpy', reason='audio extra not installed')
pytest.importorskip('librosa', reason='audio extra not installed')

from ep133_mcp.audio import kit
from ep133_mcp.audio.errors import InvalidReference
from ep133_mcp.safety import preflight
import synth_reference
from test_preflight import state

FALLBACK = {'separation': 'hpss', 'beat_tracker': 'librosa'}


@pytest.fixture(scope='module')
def extracted(tmp_path_factory):
    truth = synth_reference.write(tmp_path_factory.mktemp('ref') / 'ref')
    return truth, kit.extract_kit(truth['clip'], **FALLBACK)


def by_class(record, cls):
    return [s for s in record['slices'] if s['class'] == cls]


def test_drum_classes_recovered(extracted):
    truth, record = extracted
    assert record['status'] == 'extracted' and record['probable'] is True
    clusters = record['drum_clusters']
    assert list(clusters) == ['kick', 'snare', 'hat']
    assert clusters['kick']['centroid_hz'] < clusters['snare']['centroid_hz'] < clusters['hat']['centroid_hz']
    assert {s['pad']: s['class'] for s in record['slices'] if s['pad'] <= 3} == {1: 'kick', 2: 'snare', 3: 'hat'}
    # Each drum exemplar sits on one of the generator's hits of that class.
    for cls in ('kick', 'snare', 'hat'):
        onset = by_class(record, cls)[0]['onset_s']
        assert min(abs(onset - s) for name, s in truth['events'] if name == cls) < 0.05


def test_bass_and_melody_notes_recovered(extracted):
    truth, record = extracted
    assert sorted(s['note'] for s in by_class(record, 'bass')) == truth['bass_notes']
    assert sorted(s['note'] for s in by_class(record, 'melodic')) == truth['melody_notes']
    assert record['unfilled'] == [{'pad': 9, 'class': 'bass', 'reason': 'no confident bass note'}]


def test_every_slice_passes_preflight(extracted):
    _, record = extracted
    samples = [(s['pad'], preflight.read_sample(s['path'])) for s in record['slices']]
    assert all(sample.duration_seconds <= kit.MAX_SLICE_S for _, sample in samples)
    assert sum(len(sample.pcm) for _, sample in samples) == record['required_pcm_bytes']
    assert [s['pcm_bytes'] for s in record['slices']] == [len(sample.pcm) for _, sample in samples]
    plan = preflight.prepare(samples, 1, 'A', state(), record['required_pcm_bytes'] + 1)
    assert len(plan) == len(samples)
    with pytest.raises(preflight.TooLarge):
        preflight.prepare(samples, 1, 'A', state(), record['required_pcm_bytes'])


def test_slice_shape(extracted):
    import soundfile as sf

    _, record = extracted
    for s in record['slices']:
        audio, sr = sf.read(s['path'], dtype='float32')
        assert sr == kit.DEVICE_RATE and audio.ndim == 1
        assert abs(float(np.max(np.abs(audio))) - kit.PEAK) < 0.02
        fade = int(kit.DEVICE_RATE * kit.FADE_S)
        assert abs(audio[-1]) < 1e-3 and np.max(np.abs(audio[-fade:])) <= kit.PEAK
        assert Path(s['path']).name == f"{s['pad']:02d}_{s['class']}.wav"
        assert s['source_s'][0] < s['source_s'][1] <= s['source_s'][0] + kit.MAX_SLICE_S + 1e-6


def test_kit_json_and_mapping(extracted):
    _, record = extracted
    saved = json.loads(Path(record['kit']).read_text())
    assert saved['slices'] == record['slices'] and 'status' not in saved
    assert record['install_mapping'] == [{'pad': s['pad'], 'path': s['path']} for s in record['slices']]
    assert Path(record['kit_dir']) == Path(record['clip']).parent / 'kit'


def test_custom_want_and_rewrite(extracted):
    truth, _ = extracted
    record = kit.extract_kit(truth['clip'], want=['bass', 'kick'], **FALLBACK)
    assert [(s['pad'], s['class']) for s in record['slices']] == [(1, 'bass'), (2, 'kick')]
    assert record['unfilled'] == []
    assert sorted(p.name for p in Path(record['kit_dir']).glob('*.wav')) == ['01_bass.wav', '02_kick.wav']
    assert kit.extract_kit(truth['clip'], want='kick, hat', **FALLBACK)['want'] == ['kick', 'hat']


@pytest.mark.parametrize('want', [[], ['kick'] * 13, ['cowbell'], 'snare,tom'])
def test_bad_want_rejected(extracted, want):
    truth, _ = extracted
    with pytest.raises(InvalidReference):
        kit.extract_kit(truth['clip'], want=want, **FALLBACK)


def test_cluster_degenerate_cases():
    assert kit.cluster_drums(np.zeros((0, 3))) == {}
    assert kit.cluster_drums(np.array([[300.0, 0.2, 0.7]])) == {'kick': [0]}
    two = kit.cluster_drums(np.array([[300.0, 0.2, 0.7], [9000.0, 0.6, 0.0], [300.0, 0.2, 0.7]]))
    assert two == {'kick': [0, 2], 'hat': [1]}
