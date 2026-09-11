"""Serialized install orchestration: full preflight, confirmation, durable steps."""

import hashlib
import json
import secrets
import time
import tarfile

from ..device import DeviceError
from ..protocol.projects import stored_pads
from .backup import snapshot
from .errors import InvalidDestination, VerificationFailed
from .preflight import prepare, read_sample, validate_destination


def device_id(live):
    return hashlib.sha256((live['sku'] + ':' + live['serial']).encode()).hexdigest()


def pad_record(device, project, group, pad):
    for record in stored_pads(device.project_tar(project)):
        if record['group'] == group and record['pad'] == pad:
            return record['stored_slot'], record['stored_length']
    raise VerificationFailed('Stored pad record missing', next_step='Reconnect and inspect the project.')


class Installer:
    def __init__(self, backups, journal):
        self.backups = backups
        self.journal = journal
        self._confirmations = {}

    def install(self, mapping, project, group, backup_id, device, confirm=None):
        if not mapping or len(mapping) > 12:
            raise InvalidDestination('Expected 1..12 pad/path entries', next_step='Provide a sample mapping.')
        for entry in mapping:
            if not isinstance(entry, dict) or set(entry) != {'pad', 'path'}:
                raise InvalidDestination('Each entry needs only pad and path', next_step='Correct the mapping.')
            validate_destination(project, group, entry['pad'])
            if not isinstance(entry['path'], str):
                raise InvalidDestination('Sample path must be a string', next_step='Provide a local WAV path.')
        live = self.backups.require_current(backup_id, device)
        samples = [(entry['pad'], read_sample(entry['path'])) for entry in mapping]
        device.greet()
        device.begin_read()
        plan = prepare(samples, project, group, live, device.sample_root().free_space_in_bytes)
        impact = [{k: v for k, v in entry.items() if k != 'sample'} | {
            'crc': entry['sample'].crc, 'frames': entry['sample'].frames,
            'pcm_sha256': hashlib.sha256(entry['sample'].pcm).hexdigest(),
        } for entry in plan]
        binding = hashlib.sha256(json.dumps({'backup_id': backup_id,
            'device_id': device_id(live), 'impact': impact}, sort_keys=True).encode()).hexdigest()
        if any(entry['destructive'] for entry in plan):
            previous = self._confirmations.pop(confirm, None) if confirm else None
            if previous is None or previous[0] != binding or previous[1] < time.monotonic():
                self._confirmations = {k: v for k, v in self._confirmations.items()
                                       if v[1] >= time.monotonic()}
                token = secrets.token_urlsafe(32)
                self._confirmations[token] = (binding, time.monotonic() + 300)
                return {'status': 'needs_confirmation', 'impact': impact, 'confirm': token,
                        'instruction': 'Show this exact impact to the owner; repeat only after approval.'}
        # Persist intent before the first frame that can mutate the device.
        entries = [entry | {'status': 'pending'} for entry in impact]
        record = self.journal.create(device_id(live), backup_id, entries)
        self.backups.invalidate()
        for index, (item, entry) in enumerate(zip(plan, entries)):
            sample = item['sample']
            try:
                # State can change during a kit. Re-read the destination and memory.
                fresh = snapshot(device)
                if any(fresh[k] != live[k] for k in ('sku', 'os_version', 'serial', 'slots', 'pads')):
                    raise VerificationFailed('Device state changed after preflight',
                                             next_step='Inspect the device and verify a fresh backup.')
                current = fresh['pads'][project, group, item['pad']]
                if current != (item['prior_slot'], item['prior_length']):
                    raise VerificationFailed('Destination changed after preflight', observed=current,
                                             next_step='Inspect pads and create a fresh backup.')
                device.begin_read()
                if device.slot_exists(item['slot']):
                    raise VerificationFailed('Reserved slot became occupied', observed=item['slot'],
                                             next_step='Inspect library and create a fresh backup.')
                remaining = [(p['pad'], p['sample']) for p in plan[index:]]
                prepare(remaining, project, group, live, device.sample_root().free_space_in_bytes)
                entry['status'] = 'upload_attempted'
                self.journal.save(record)
                device.upload_sample(item['slot'], sample.name, sample.pcm)
                device.greet()
                device.begin_read()
                meta = device.metadata(item['slot'])
                if not isinstance(meta, dict) or meta.get('crc') != sample.crc or meta.get('sample.end') != sample.frames:
                    raise VerificationFailed('Uploaded sample CRC or frame count differs',
                                             observed=meta, expected={'crc': sample.crc, 'sample.end': sample.frames},
                                             next_step='Inspect the orphaned slot; no pad was assigned.')
                entry['status'] = 'assignment_attempted'
                self.journal.save(record)
                device.assign_pad(item['node'], item['slot'])
                actual = pad_record(device, project, group, item['pad'])
                if actual != (item['slot'], sample.frames):
                    raise VerificationFailed('Stored pad assignment differs after write', observed=actual,
                                             expected=(item['slot'], sample.frames),
                                             next_step='Inspect the journal and pad before retrying.')
                live['slots'].add(item['slot'])
                live['pads'][project, group, item['pad']] = actual
                entry['status'] = 'installed'
                self.journal.save(record)
            except (DeviceError, ValueError, OSError, tarfile.TarError) as e:
                entry['failure'] = {'error': type(e).__name__, 'message': str(e)}
                record['status'] = 'partial'
                self.journal.save(record)
                return self._result(record)
        record['status'] = 'installed'
        self.journal.save(record)
        return self._result(record)

    @staticmethod
    def _result(record):
        return {'status': record['status'], 'journal_id': record['id'],
                'entries': record['entries'], 'transactional': False,
                'power_cycle_verified': False,
                'next_step': 'Create and verify a fresh Sample Tool backup before another install.'}

    def undo(self, device):
        greeting = device.greet()
        identity = device_id({'sku': greeting.sku, 'serial': greeting.serial})
        record = self.journal.latest(identity)
        if record is None:
            return {'status': 'nothing_to_undo'}
        if record['status'] == 'undone':
            return self._result(record)
        self.backups.invalidate()
        for entry in reversed(record['entries']):
            if entry['status'] not in ('installed', 'assignment_attempted', 'undo_attempted'):
                continue
            current = pad_record(device, entry['project'], entry['group'], entry['pad'])
            prior = (entry['prior_slot'], entry['prior_length'])
            if entry['status'] == 'undo_attempted' and current == prior:
                entry['status'] = 'undone'
                self.journal.save(record)
                continue
            if current != (entry['slot'], entry['frames']):
                entry['undo_failure'] = 'Pad changed since install; no overwrite attempted.'
                self.journal.save(record)
                continue
            device.begin_read()
            if entry['prior_slot'] != 0 and not device.slot_exists(entry['prior_slot']):
                entry['undo_failure'] = 'Prior stored slot is absent; restoring stale records is unverified.'
                self.journal.save(record)
                continue
            entry['status'] = 'undo_attempted'
            self.journal.save(record)
            try:
                device.assign_pad(entry['node'], entry['prior_slot'])
                actual = pad_record(device, entry['project'], entry['group'], entry['pad'])
                if actual != prior:
                    raise VerificationFailed('Undo did not reproduce prior stored slot/length', observed=actual,
                                             expected=prior, next_step='Inspect the pad and journal.')
                entry['status'] = 'undone'
                entry.pop('undo_failure', None)
                self.journal.save(record)
            except (DeviceError, ValueError, OSError, tarfile.TarError) as e:
                entry['undo_failure'] = str(e)
                self.journal.save(record)
                break
        record['status'] = ('undone' if all(e['status'] in ('undone', 'pending')
                                           for e in record['entries']) else 'undo_partial')
        self.journal.save(record)
        result = self._result(record)
        result['library_slots_left_in_place'] = [e['slot'] for e in record['entries'] if e['status'] != 'pending']
        return result
