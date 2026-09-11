import struct
import wave

import pytest

from ep133_mcp.safety.preflight import Sample, read_sample, prepare, validate_destination
from ep133_mcp.safety.errors import InvalidDestination, NoSafeSlot, TooLarge, UnsupportedFormat


def wav_file(tmp_path, *, channels=1, rate=46875, width=2, frames=10):
    path = tmp_path / 'sample.wav'
    with wave.open(str(path), 'wb') as w:
        w.setparams((channels, width, rate, frames, 'NONE', 'not compressed'))
        w.writeframes(bytes(frames * width * channels))
    return path


def state():
    return {'slots': {0, 1}, 'pads': {(p, g, n): (0, 0)
            for p in range(1, 10) for g in 'ABCD' for n in range(1, 13)}}


@pytest.mark.parametrize('kw', [{'channels': 2}, {'rate': 44100}, {'width': 3}, {'frames': 0}])
def test_bad_format_rejected(tmp_path, kw):
    with pytest.raises(UnsupportedFormat):
        read_sample(wav_file(tmp_path, **kw))


def test_real_length_and_crc(tmp_path):
    import zlib
    path = wav_file(tmp_path)
    sample = read_sample(path)
    assert sample.frames == 10 and len(sample.pcm) == 20
    assert sample.crc == zlib.crc32(bytes(20))
    path.write_bytes(path.read_bytes()[:-2])
    with pytest.raises(UnsupportedFormat, match='completely'):
        read_sample(path)


def test_oversized_stereo_rejected_before_loading_pcm(tmp_path):
    path = wav_file(tmp_path, channels=2)
    data = bytearray(path.read_bytes())
    struct.pack_into('<I', data, 40, 100_000_000)
    path.write_bytes(data)
    with pytest.raises(UnsupportedFormat):
        read_sample(path)


@pytest.mark.parametrize('args', [(0,'A',1), (10,'A',1), (1,'AB',1), (1,'a',1),
                                  (1,'A',0), (1,'A',13), (True,'A',1), (1,'A',1.5)])
def test_destination_bounds(args):
    with pytest.raises(InvalidDestination):
        validate_destination(*args)


def test_all_projects_and_zero_lengths_reserve_slots():
    live = state()
    live['pads'][9,'D',12] = (2, 0)
    live['pads'][8,'C',1] = (3, 10)
    plan = prepare([(1, Sample('tone', bytes(20), 10, 0))], 1, 'A', live, 100)
    assert plan[0]['slot'] == 4
    assert plan[0]['destructive'] is True  # slot 0 actually exists
    live['slots'].remove(0)
    assert prepare([(1, plan[0]['sample'])], 1, 'A', live, 100)[0]['destructive'] is False


def test_size_equality_and_total_kit_size():
    sample = Sample('tone', bytes(20), 10, 0)
    with pytest.raises(TooLarge) as error:
        prepare([(1, sample), (2, sample)], 1, 'A', state(), 40)
    assert error.value.detail['maximum_duration_seconds'] == 19 / 46875
    assert len(prepare([(1, sample), (2, sample)], 1, 'A', state(), 41)) == 2


def test_no_slots_and_duplicate_pads():
    live = state()
    live['slots'] = set(range(1000))
    sample = Sample('tone', bytes(20), 10, 0)
    with pytest.raises(NoSafeSlot):
        prepare([(1, sample)], 1, 'A', live, 100)
    with pytest.raises(InvalidDestination):
        prepare([(1, sample), (1, sample)], 1, 'A', live, 100)
