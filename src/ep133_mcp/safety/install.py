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
    try:
        records = stored_pads(device.project_tar(project))
    except (ValueError, tarfile.TarError) as e:
        raise VerificationFailed('Invalid stored project records', observed=str(e),
                                 next_step='Inspect the device and journal before retrying.') from e
    for record in records:
        if record['group'] == group and record['pad'] == pad:
            return record['stored_slot'], record['stored_length']
    raise VerificationFailed('Stored pad record missing', next_step='Reconnect and inspect the project.')


def verify_prior(device, project, group, pad, prior):
    """Prove an undo write reproduced `prior`; returns a note, or None when the record matches.

    A cleared pad (prior slot 0) is proven by stored slot 0 and a pad JSON that resolves to
    sym 0: when the clearing write lands in a later power session than the assignment, the
    device keeps the old length in the project record (P1 B09 read (0, 175813) after sym 0
    with JSON {sym: 0}, 2026-09-12; before a power-cycle the same write read (0, 0)). The
    length field is independent of the slot field (docs/research/backup-verification.md), so
    that leftover is reported, not treated as a failed undo."""
    actual = pad_record(device, project, group, pad)
    if actual == prior:
        return None
    if prior[0] == 0 and actual[0] == 0:
        device.begin_read()
        if int(device.pad_metadata(project, group, pad).get('sym') or 0) == 0:
            return f'pad cleared (JSON sym 0); the device left stored length {actual[1]} in the project record'
    raise VerificationFailed('Undo did not reproduce prior stored slot/length', observed=actual,
                             expected=prior, next_step='Inspect the pad and journal.')


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
                after_upload = snapshot(device)
                expected_slots = live['slots'] | {item['slot']}
                if (after_upload['slots'] != expected_slots or after_upload['pads'] != live['pads']
                        or any(after_upload[k] != live[k] for k in ('sku', 'os_version', 'serial'))):
                    raise VerificationFailed('Device changed during upload; sample left unassigned',
                                             next_step='Inspect the orphaned slot and verify a fresh backup.')
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
            if entry['status'] == 'undo_attempted' and self._settle(device, entry, prior, record):
                continue
            if current != (entry['slot'], entry['frames']):
                entry['undo_failure'] = 'Pad changed since install; no overwrite attempted.'
                self.journal.save(record)
                continue
            device.begin_read()
            if entry['prior_slot'] != 0 and not device.slot_exists(entry['prior_slot']):
                # Same rule as the chop undo: a stale prior is an empty pad, so clear it.
                entry['undo_note'] = (f"prior slot {entry['prior_slot']} is absent from the library; "
                                      'pad cleared instead of re-pointed at it')
                prior = (0, 0)
            entry['status'] = 'undo_attempted'
            self.journal.save(record)
            try:
                device.assign_pad(entry['node'], prior[0])
                note = verify_prior(device, entry['project'], entry['group'], entry['pad'], prior)
                if note:
                    entry['undo_note'] = '; '.join(filter(None, (entry.get('undo_note'), note)))
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

    def _settle(self, device, entry, prior, record):
        """An interrupted undo whose write did land: mark it undone without writing again."""
        try:
            note = verify_prior(device, entry['project'], entry['group'], entry['pad'], prior)
        except VerificationFailed:
            return False
        if note:
            entry['undo_note'] = note
        entry['status'] = 'undone'
        entry.pop('undo_failure', None)
        self.journal.save(record)
        return True
