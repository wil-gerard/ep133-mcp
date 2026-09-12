import io
import json
from pathlib import Path
import os
import time
import zipfile
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ep133_mcp.safety.backup import BackupRegistry, read_backup
from ep133_mcp.safety.errors import BackupStale, InvalidBackup
from test_projects import archive


def make_backup(tmp_path, *, missing=None, meta=None):
    path = tmp_path / 'backup.pak'
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('/meta.json', json.dumps(meta or {
            'device_name': 'EP-133', 'device_sku': 'TE032AS001',
            'device_version': '2.5.1', 'pak_type': 'user'}))
        for p in range(1, 10):
            if p != missing:
                z.writestr(f'/projects/P{p:02}.tar', archive())
        z.writestr('/sounds/016 tone.wav', (Path(__file__).parents[1] / 'fixtures/phase0-test-tone.wav').read_bytes())
    return path


def fake_device():
    return SimpleNamespace(
        greet=Mock(return_value=SimpleNamespace(sku='TE032AS001', os_version='2.5.1',
                                                product='EP-133', serial='test')),
        begin_read=Mock(), metadata=Mock(return_value={}), slot_exists=Mock(side_effect=lambda slot: slot == 16),
        project_tar=Mock(return_value=archive()))


def test_current_backup_revalidated_before_use(tmp_path):
    path, device = make_backup(tmp_path), fake_device()
    registry = BackupRegistry()
    result = registry.verify(path, device)
    assert result['status'] == 'current'
    assert result['pads_compared'] == 432
    assert 'serial' not in result
    registry.require_current(result['backup_id'], device)
    assert device.project_tar.call_count == 18
    assert device.slot_exists.call_count == 1998
    device.slot_exists.side_effect = lambda slot: slot in (16, 17)
    with pytest.raises(BackupStale):
        registry.require_current(result['backup_id'], device)
    with pytest.raises(BackupStale):
        registry.require_current(result['backup_id'], device)


def test_stale_stored_field_cannot_hide_behind_resolved_zero(tmp_path):
    path, device = make_backup(tmp_path), fake_device()
    original = device.project_tar.return_value
    # Change a zero-length pad's stored id without altering its length.
    changed = bytearray(original)
    changed[513:515] = (99).to_bytes(2, 'little')
    device.project_tar.return_value = bytes(changed)
    result = BackupRegistry().verify(path, device)
    assert result['status'] == 'stale'
    assert len(result['differences']) == 9
    assert result['differences'][0]['device'] == {'slot': 99, 'length': 0}


@pytest.mark.parametrize('missing', [1, 9])
def test_missing_project_rejected(tmp_path, missing):
    with pytest.raises(InvalidBackup):
        read_backup(make_backup(tmp_path, missing=missing))


def test_invalid_zip_and_duplicate_members(tmp_path):
    path = tmp_path / 'bad.pak'
    path.write_bytes(b'not a zip')
    with pytest.raises(InvalidBackup):
        read_backup(path)
    path = make_backup(tmp_path)
    with zipfile.ZipFile(path, 'a') as z:
        with pytest.warns(UserWarning):
            z.writestr('/meta.json', '{}')
    with pytest.raises(InvalidBackup, match='validated'):
        read_backup(path)


def test_age_and_identity_rejected(tmp_path):
    path, device = make_backup(tmp_path), fake_device()
    os.utime(path, (time.time() - 100, time.time() - 100))
    assert BackupRegistry(10).verify(path, device)['status'] == 'stale'
    device.greet.return_value.sku = 'OTHER'
    result = BackupRegistry().verify(path, device)
    assert result['differences'][0]['field'] == 'sku'


def test_changed_archive_or_device_invalidates_registration(tmp_path):
    path, device = make_backup(tmp_path), fake_device()
    registry = BackupRegistry()
    result = registry.verify(path, device)
    device.greet.return_value.serial = 'different'
    with pytest.raises(BackupStale):
        registry.require_current(result['backup_id'], device)
    result = registry.verify(path, device)
    with path.open('ab') as f:
        f.write(b'changed')
    with pytest.raises(BackupStale):
        registry.require_current(result['backup_id'], device)


def test_device_failure_never_registers_backup(tmp_path):
    from ep133_mcp.device import DeviceError
    path, device = make_backup(tmp_path), fake_device()
    registry = BackupRegistry()
    result = registry.verify(path, device)
    device.project_tar.side_effect = DeviceError('disconnected')
    with pytest.raises(DeviceError):
        registry.verify(path, device)
    with pytest.raises(BackupStale):
        registry.require_current(result['backup_id'], device)


def test_invalid_audio_cannot_authorize_writes(tmp_path):
    path = make_backup(tmp_path)
    with zipfile.ZipFile(path, 'a') as z:
        z.writestr('/sounds/017 corrupt.wav', b'not audio')
    with pytest.raises(InvalidBackup):
        read_backup(path)


@pytest.mark.parametrize('age', [0, -1, float('inf'), float('nan')])
def test_age_configuration_must_be_finite_positive(age):
    with pytest.raises(ValueError):
        BackupRegistry(age)


def test_empty_slot_zero_is_not_library_audio(tmp_path):
    path, device = make_backup(tmp_path), fake_device()
    device.slot_exists.side_effect = lambda slot: slot in (0, 16)
    registry = BackupRegistry()
    assert registry.verify(path, device)['status'] == 'current'
    device.metadata.return_value = {'name': 'unexpected audio', 'crc': 12}
    with pytest.raises(InvalidBackup, match='sentinel'):
        registry.verify(path, device)


class CapturingDevice:
    """Reads like the real device: project TARs, slot metadata, and raw PCM per slot."""

    def __init__(self, pcm):
        from types import SimpleNamespace
        self.pcm = pcm
        self.greeting = SimpleNamespace(sku='TE032AS001', os_version='2.5.1', serial='s', product='EP-133')
        self.reads = []

    def greet(self):
        return self.greeting

    def begin_read(self):
        return None

    def slot_exists(self, slot):
        return slot in self.pcm

    def metadata(self, slot):
        import zlib
        if slot not in self.pcm:
            return None
        data = self.pcm[slot]
        return {'name': f'sound {slot}', 'crc': zlib.crc32(data), 'sample.end': len(data) // 2,
                'samplerate': 46875, 'channels': 1, 'format': 's16'}

    def slot_pcm(self, slot):
        self.reads.append(slot)
        return self.pcm[slot]

    def project_tar(self, project):
        from test_pack_project import minimal_project
        from ep133_mcp.protocol import projects as P
        return P.pack_project(minimal_project())


def test_create_backup_reads_the_device_and_reuses_by_crc(tmp_path):
    """Backups take tens of minutes at 25 KiB/s, so an unchanged slot must never be re-read."""
    import wave
    import zipfile
    from ep133_mcp.safety.capture import create_backup
    from ep133_mcp.protocol.projects import read_pak

    device = CapturingDevice({3: b'\x01\x02' * 100, 7: b'\x03\x04' * 50})
    first = tmp_path / 'one.pak'
    report = create_backup(device, first)
    assert report['status'] == 'written' and report['slots'] == 2 and report['slots_read'] == 2
    assert report['slots_reused'] == 0 and device.reads == [3, 7]
    meta, projects, sounds = read_pak(first.read_bytes())
    assert meta['pak_type'] == 'user' and meta['device_version'] == '2.5.1'
    assert sorted(projects) == list(range(1, 10))
    assert sorted(sounds) == ['/sounds/003 sound 3.wav', '/sounds/007 sound 7.wav']
    with wave.open(io.BytesIO(sounds['/sounds/003 sound 3.wav'])) as w:
        assert w.getframerate() == 46875 and w.getnchannels() == 1 and w.getsampwidth() == 2
        assert w.readframes(w.getnframes()) == device.pcm[3]        # byte-exact audio

    # Second backup with the first as base: unchanged slots are copied, changed ones re-read.
    device.reads.clear()
    device.pcm[7] = b'\xaa\xbb' * 50
    second = tmp_path / 'two.pak'
    again = create_backup(device, second, base=first)
    assert again['slots_reused'] == 1 and again['slots_read'] == 1 and device.reads == [7]
    _, _, sounds2 = read_pak(second.read_bytes())
    with wave.open(io.BytesIO(sounds2['/sounds/007 sound 7.wav'])) as w:
        assert w.readframes(w.getnframes()) == device.pcm[7]

    with pytest.raises(InvalidBackup):
        create_backup(device, second)                                # never overwrites
    with pytest.raises(InvalidBackup):
        create_backup(device, tmp_path / 'x.ppak')


def test_create_backup_records_a_slot_it_could_not_read(tmp_path):
    from ep133_mcp.device import DeviceError
    from ep133_mcp.safety.capture import create_backup

    device = CapturingDevice({3: b'\x01\x02' * 10, 9: b'\x05\x06' * 10})

    def explode(slot):
        if slot == 9:
            raise DeviceError('slot read does not match its stored crc')
        return device.pcm[slot]

    device.slot_pcm = explode
    report = create_backup(device, tmp_path / 'partial.pak')
    assert report['status'] == 'partial'
    assert report['problems'] == [{'slot': 9, 'problem': 'DeviceError',
                                   'message': 'slot read does not match its stored crc'}]
    assert report['slots_read'] == 1


def wav_chunks(data):
    import struct
    out, i = {}, 12
    while i + 8 <= len(data):
        cid = data[i:i + 4].decode('latin1')
        size = struct.unpack_from('<I', data, i + 4)[0]
        out[cid] = data[i + 8:i + 8 + size]
        i += 8 + size + (size & 1)
    return out


BASE_META = {'format': 's16', 'channels': 1, 'samplerate': 46875, 'sound.rootnote': 60,
             'sound.playmode': 'oneshot', 'sound.pitch': 0.0, 'sound.pan': 0, 'sound.amplitude': 100,
             'envelope.attack': 0, 'envelope.release': 255, 'time.mode': 'off',
             'sample.start': 0, 'sample.end': 2, 'sound.loopstart': -1, 'sound.loopend': -1,
             'sound.bpm': 0.0}


def test_wav_carries_the_devices_own_settings():
    """A bare 44-byte header loses every sample's playmode, tuning, envelope and loop on restore.

    These chunk shapes were read out of a Sample Tool backup and reproduce all 56 of its WAVs
    byte for byte."""
    import struct
    from ep133_mcp.safety.capture import wav_bytes

    chunks = wav_chunks(wav_bytes(b'\x01\x02\x03\x04', BASE_META))
    assert list(chunks) == ['fmt ', 'smpl', 'LIST', 'data']          # no acid without a bpm
    assert chunks['data'] == b'\x01\x02\x03\x04'
    assert struct.unpack_from('<I', chunks['smpl'], 12)[0] == 60     # MIDI unity note
    assert struct.unpack_from('<I', chunks['smpl'], 28)[0] == 0      # no loops
    assert chunks['LIST'][:8] == b'INFOTNGE'
    payload = chunks['LIST'][12:]
    assert b'"sound.playmode":"oneshot"' in payload and b'"sound.pitch":0' in payload
    assert len(payload) % 4 == 0 and payload.endswith(b'\x00')       # terminated, then padded to 4


def test_wav_records_a_loop_that_starts_at_zero():
    """sound.loopstart 0 is a real loop point; testing it for truthiness drops the loop."""
    import struct
    from ep133_mcp.safety.capture import wav_bytes

    chunks = wav_chunks(wav_bytes(b'\x00' * 8, BASE_META | {'sound.loopstart': 0, 'sound.loopend': 3}))
    assert len(chunks['smpl']) == 60 and struct.unpack_from('<I', chunks['smpl'], 28)[0] == 1
    assert struct.unpack_from('<2I', chunks['smpl'], 44) == (0, 3)
    assert chunks['LIST'][12:].startswith(b'{"sound.loopstart":0,"sound.loopend":3,')


def test_wav_records_a_tempo():
    import struct
    from ep133_mcp.safety.capture import wav_bytes

    chunks = wav_chunks(wav_bytes(b'\x00' * 4, BASE_META | {'sound.bpm': 90.0}))
    assert 'acid' in chunks and struct.unpack_from('<f', chunks['acid'], 20)[0] == pytest.approx(90.0)
    assert b'"sound.bpm":90,' in chunks['LIST']      # whole numbers are written as integers


def test_backup_stays_valid_while_the_device_only_loses_content(tmp_path):
    path, device = make_backup(tmp_path), fake_device()
    registry = BackupRegistry()
    result = registry.verify(path, device)
    assert result['status'] == 'current'
    # Our own delete removed the only slot: the backup still holds it - a superset.
    device.slot_exists.side_effect = lambda slot: False
    live = registry.require_current(result['backup_id'], device)
    assert live['slots'] == set()
    assert registry.verify(path, device)['status'] == 'superset'
    # A pad cleared since is the same story...
    original = device.project_tar.return_value
    cleared = bytearray(original)
    cleared[513:515] = (0).to_bytes(2, 'little')
    cleared[520:524] = (0).to_bytes(4, 'little')
    device.project_tar.return_value = bytes(cleared)
    assert registry.verify(path, device)['status'] in ('superset', 'current')
    # ...but a slot the device has and the backup lacks is content a restore would lose.
    device.slot_exists.side_effect = lambda slot: slot == 17
    assert registry.verify(path, device)['status'] == 'stale'
    with pytest.raises(BackupStale):
        registry.require_current(result['backup_id'], device)
