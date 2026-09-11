"""Read Sample Tool backups without extracting files; compare stored fields."""

from dataclasses import dataclass
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import tarfile
import time
import zipfile
import wave
import zlib

from ..protocol.projects import stored_pads
from .errors import BackupStale, InvalidBackup

# Generous bounds above the device's 64 MB library, still bounded before inflate.
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_PROJECT_BYTES = 2 * 1024 * 1024
LIBRARY_SLOTS = range(1, 1000)  # 0 is the empty-assignment sentinel; root is 1000


@dataclass
class Backup:
    path: Path
    backup_id: str
    modified_at: float
    meta: dict
    slots: set[int]
    pads: dict


def read_backup(path: str | Path) -> Backup:
    path = Path(path).expanduser()
    try:
        with path.open('rb') as source:
            data = source.read(MAX_ARCHIVE_BYTES + 1)
            modified_at = os.fstat(source.fileno()).st_mtime
        if len(data) > MAX_ARCHIVE_BYTES:
            raise ValueError('archive exceeds 128 MiB limit')
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            members = z.infolist()
            names = [m.filename for m in members]
            if len(names) != len(set(names)):
                raise ValueError('duplicate archive members')
            if sum(m.file_size for m in members) > MAX_ARCHIVE_BYTES:
                raise ValueError('expanded archive exceeds 128 MiB limit')
            if any('..' in n.split('/') or '\\' in n for n in names):
                raise ValueError('unsafe archive member name')
            if z.testzip() is not None:
                raise ValueError('archive checksum failure')
            meta = json.loads(z.read('/meta.json'))
            if not isinstance(meta, dict) or meta.get('pak_type') != 'user':
                raise ValueError('expected a full user backup')
            if not all(isinstance(meta.get(k), str) and meta[k]
                       for k in ('device_name', 'device_sku', 'device_version')):
                raise ValueError('missing device identity')
            slots = set()
            for member in members:
                name = member.filename
                if name.startswith('/sounds/') and not member.is_dir():
                    match = re.fullmatch(r'/sounds/(\d+) [^/]+\.wav', name)
                    if match is None:
                        raise ValueError('unrecognized sound member')
                    slot = int(match[1])
                    if slot not in LIBRARY_SLOTS or slot in slots:
                        raise ValueError('duplicate or invalid library slot')
                    with wave.open(io.BytesIO(z.read(member)), 'rb') as wav:
                        frames = wav.getnframes()
                        expected = frames * wav.getnchannels() * wav.getsampwidth()
                        if frames <= 0 or expected > MAX_ARCHIVE_BYTES or len(wav.readframes(frames)) != expected:
                            raise ValueError('empty, oversized or truncated backup sound')
                    slots.add(slot)
            pads = {}
            for project in range(1, 10):
                name = f'/projects/P{project:02}.tar'
                if z.getinfo(name).file_size > MAX_PROJECT_BYTES:
                    raise ValueError('project exceeds size limit')
                for pad in stored_pads(z.read(name)):
                    pads[project, pad['group'], pad['pad']] = (
                        pad['stored_slot'], pad['stored_length'])
        return Backup(path.resolve(), hashlib.sha256(data).hexdigest(), modified_at,
                      meta, slots, pads)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, tarfile.TarError,
            RuntimeError, NotImplementedError, EOFError, wave.Error, zlib.error) as e:
        raise InvalidBackup('Backup archive could not be validated', observed=str(e),
                            expected='Complete Sample Tool user .pak',
                            next_step='Create a fresh full backup with EP Sample Tool.') from e


def snapshot(device) -> dict:
    greeting = device.greet()
    if not greeting.sku or not greeting.os_version or not greeting.serial:
        raise InvalidBackup('Device identity is incomplete',
                            next_step='Reconnect and retry device_info before verification.')
    device.begin_read()
    sentinel = device.metadata(0)
    if sentinel != {}:
        raise InvalidBackup('Slot zero did not match the observed empty sentinel',
                            observed=sentinel, expected={},
                            next_step='Inspect slot zero before verifying this firmware.')
    slots = {slot for slot in LIBRARY_SLOTS if device.slot_exists(slot)}
    pads = {}
    try:
        for project in range(1, 10):
            for pad in stored_pads(device.project_tar(project)):
                pads[project, pad['group'], pad['pad']] = (
                    pad['stored_slot'], pad['stored_length'])
    except (ValueError, tarfile.TarError) as e:
        raise InvalidBackup('Live stored pad records could not be validated',
                            observed=str(e), next_step='Reconnect and retry verification.') from e
    return {'sku': greeting.sku, 'os_version': greeting.os_version,
            'product': greeting.product, 'serial': greeting.serial,
            'slots': slots, 'pads': pads}


def differences(backup: Backup, live: dict) -> list[dict]:
    result = []
    for field, key in (('sku', 'device_sku'), ('os_version', 'device_version')):
        if backup.meta[key] != live[field]:
            result.append({'field': field, 'backup': backup.meta[key], 'device': live[field]})
    if backup.meta['device_name'] != 'EP-133':
        result.append({'field': 'device_name', 'backup': backup.meta['device_name'],
                       'expected': 'EP-133'})
    if backup.slots != live['slots']:
        result.append({'field': 'library_slots',
                       'only_in_backup': sorted(backup.slots - live['slots']),
                       'only_on_device': sorted(live['slots'] - backup.slots)})
    for (project, group, pad), old in backup.pads.items():
        new = live['pads'][project, group, pad]
        if old != new:
            result.append({'field': 'stored_pad', 'project': project, 'group': group,
                           'pad': pad, 'backup': {'slot': old[0], 'length': old[1]},
                           'device': {'slot': new[0], 'length': new[1]}})
    return result


class BackupRegistry:
    def __init__(self, max_age_seconds: float = 86400):
        if not math.isfinite(max_age_seconds) or max_age_seconds <= 0:
            raise ValueError('backup age must be positive')
        self.max_age_seconds = max_age_seconds
        self._verified = {}

    def verify(self, path, device):
        backup = read_backup(path)
        self._verified.pop(backup.backup_id, None)
        live = snapshot(device)
        diff = differences(backup, live)
        age = time.time() - backup.modified_at
        if age < -60 or age > self.max_age_seconds:
            diff.append({'field': 'backup_age_seconds', 'observed': age,
                         'maximum': self.max_age_seconds})
        if not diff:
            self._verified[backup.backup_id] = (backup.path, live['serial'])
        return {'status': 'stale' if diff else 'current', 'backup_id': backup.backup_id,
                'differences': diff, 'pads_compared': 432,
                'scope': 'SKU/OS, library occupancy and stored pad slot/length; not audio content'}

    def require_current(self, backup_id, device):
        entry = self._verified.get(backup_id)
        if entry is None:
            raise BackupStale('Backup is not verified in this session', observed=backup_id,
                              next_step='Run verify_backup on a current Sample Tool backup.')
        path, serial = entry
        self._verified.pop(backup_id, None)
        backup = read_backup(path)
        live = snapshot(device)
        age = time.time() - backup.modified_at
        diff = differences(backup, live)
        if backup.backup_id != backup_id or live['serial'] != serial or diff or not -60 <= age <= self.max_age_seconds:
            raise BackupStale('Backup or device changed since verification', observed=diff,
                              next_step='Create and verify a fresh Sample Tool backup.')
        self._verified[backup_id] = entry
        return live

    def invalidate(self):
        self._verified.clear()


RESTORE_PROCEDURE = {
    'type': 'guidance', 'verified_restore_available': False,
    'steps': [
        'Stop this MCP server so it releases the MIDI ports.',
        'Open EP Sample Tool, connect the device, and save a full backup of its current state.',
        'Use Sample Tool to restore the intended full .pak backup; review its overwrite warning.',
        'After restoring, save another full backup under a new filename. Keep both originals.',
        'Compare the intended and post-restore backups using tools/diff_backups.py. '
        'Check stored pad records and library slots; live sym reads alone cannot prove restoration.',
        'Close Sample Tool before restarting the server and running verify_backup.',
    ],
    'limitation': 'Restore drift was observed on hardware. Matching stored pad fields and '
                  'library occupancy does not prove every audio byte or project field was restored.',
}
