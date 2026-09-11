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
