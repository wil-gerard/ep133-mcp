"""Sample restoration shared by selective recovery and portable project import."""

import hashlib
import io
import json
import math
from pathlib import Path
import secrets
import struct
import time
import wave
import zlib

from ..device import DeviceError
from ..protocol.projects import read_pak
from .backup import snapshot
from .capture import TNGE_ORDER
from .errors import InvalidDestination, VerificationFailed
from .install import device_id
from .library import sound_index
from .preflight import MAX_PCM_BYTES
from .params import SLOT_FIELDS, _int, _number


def invalid(message, **detail):
    return InvalidDestination(message, next_step='Inspect the source and destination; no automatic overwrite or conversion.', **detail)


def parse_sound(name, data):
    if not name or len(name) > 20 or not name.isascii() or not name.isprintable():
        raise invalid('Sample name must be 1..20 printable ASCII characters', observed=name)
    if data[:4] != b'RIFF' or data[8:12] != b'WAVE' or len(data) < 12:
        raise invalid('Invalid WAV container')
    if struct.unpack_from('<I', data, 4)[0] + 8 != len(data):
        raise invalid('Truncated or trailing WAV data')
    fields = {}
    tnge_seen = False
    offset = 12
    seen = set()
    while offset < len(data):
        if offset + 8 > len(data):
            raise invalid('Truncated WAV chunk')
        tag, size = struct.unpack_from('<4sI', data, offset)
        body = data[offset + 8:offset + 8 + size]
        if len(body) != size:
            raise invalid('Truncated WAV chunk body')
        if tag in (b'fmt ', b'data') and tag in seen:
            raise invalid('Duplicate audio chunk')
        seen.add(tag)
        if tag == b'LIST' and body[:4] == b'INFO':
            pos = 4
            while pos < len(body):
                if pos + 8 > len(body):
                    raise invalid('Truncated INFO chunk')
                key, length = struct.unpack_from('<4sI', body, pos)
                value = body[pos + 8:pos + 8 + length]
                if len(value) != length:
                    raise invalid('Truncated INFO value')
                if key == b'TNGE':
                    if tnge_seen:
                        raise invalid('Duplicate TNGE metadata')
                    tnge_seen = True
                    fields = json.loads(value.rstrip(b'\0'))
                    if not isinstance(fields, dict) or set(fields) - set(TNGE_ORDER):
                        raise invalid('Unsupported TNGE metadata fields')
                    for value in fields.values():
                        if not isinstance(value, (str, int, float)) or isinstance(value, bool) or (
                                isinstance(value, (int, float)) and not math.isfinite(value)):
                            raise invalid('Invalid TNGE metadata value')
                pos += 8 + length + length % 2
        offset += 8 + size + size % 2
    if offset != len(data):
        raise invalid('Missing WAV chunk padding')
    if (b'smpl' in seen or b'acid' in seen) and not tnge_seen:
        raise invalid('Sampler metadata without TNGE is not supported for exact restoration')
    with wave.open(io.BytesIO(data)) as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getcomptype()) != (1, 2, 46875, 'NONE'):
            raise invalid('Restore supports 46875 Hz mono 16-bit PCM only')
        frames = wav.getnframes()
        pcm = wav.readframes(frames)
        if not 0 < len(pcm) <= MAX_PCM_BYTES or len(pcm) != frames * 2:
            raise invalid('Empty, truncated or oversized PCM')
    validators = SLOT_FIELDS | {'sample.start': _int(0, frames), 'sample.end': _int(1, frames),
                                'sound.bpm': _number(0, 200)}
    for key, value in fields.items():
        # Sample Tool serializes whole-number floats as ints; validate without coercing strings.
        if type(value) is float and value.is_integer():
            value = int(value)
        try:
            fields[key] = validators[key](value)
        except (KeyError, ValueError) as e:
            raise invalid('Unsupported sample metadata', observed={key: value}) from e
    if fields.get('sample.start', 0) >= fields.get('sample.end', frames):
        raise invalid('Invalid sample trim range')
    loop_start, loop_end = fields.get('sound.loopstart', -1), fields.get('sound.loopend', -1)
    if (loop_start == -1) != (loop_end == -1) or (loop_start >= 0 and not 0 <= loop_start <= loop_end <= frames):
        raise invalid('Invalid loop range')
    return {'name': name, 'pcm': pcm, 'frames': frames, 'crc': zlib.crc32(pcm),
            'sha256': hashlib.sha256(pcm).hexdigest(), 'fields': fields}


def plan_sounds(sounds, device, slot_map=None):
    indexed = sound_index(sounds)
    mapping = {}
    for key, value in (slot_map or {}).items():
        if not str(key).isdigit() or type(value) is not int or not 1 <= value <= 999:
            raise invalid('slot_map must map source slot numbers to integers 1..999')
        source = int(key)
        if source not in indexed or source in mapping:
            raise invalid('slot_map contains unknown or duplicate source', observed=key)
        mapping[source] = value
    targets = [mapping.get(s, s) for s in indexed]
    if len(targets) != len(set(targets)):
        raise invalid('Two sounds cannot map to the same destination')
    plan = []
    for source, (_, name, data) in sorted(indexed.items()):
        sample = parse_sound(name, data)
        target = mapping.get(source, source)
        action = 'upload'
        if device.slot_exists(target):
            meta = device.metadata(target)
            expected = {'name': name, **sample['fields']}
            if (not meta or meta.get('crc') != sample['crc'] or
                    any(meta.get(k) != v for k, v in expected.items()) or
                    device.slot_pcm(target) != sample['pcm']):
                raise invalid('Destination contains different audio or metadata; provide an explicit free slot_map', observed=target)
            action = 'reuse'
        plan.append({'source_slot': source, 'slot': target, 'action': action, **sample})
    required = sum(len(e['pcm']) for e in plan if e['action'] == 'upload')
    if required and required >= device.sample_root().free_space_in_bytes:
        raise invalid('Insufficient free sample memory', observed={'required_pcm_bytes': required})
    return plan


def sound_impact(plan, live):
    return [{k: v for k, v in e.items() if k != 'pcm'} | {
        'pcm_bytes': len(e['pcm']),
        'existing_references': [{'project': p, 'group': g, 'pad': n} for (p, g, n), (s, _) in live['pads'].items() if s == e['slot']],
    } for e in plan]


def upload_sounds(plan, device, journal, record, live):
    """Persist each attempted write; stop on drift, rejection or a read-back mismatch."""
    if not plan:
        return
    expected = {**live, 'slots': set(live['slots'])}
    for sample, entry in zip(plan, record['sounds']):
        fresh = snapshot(device)
        if any(fresh[k] != expected[k] for k in ('sku', 'os_version', 'serial', 'slots', 'pads')):
            raise VerificationFailed('Device changed during restore', next_step='Inspect the journal and take a fresh backup.')
        if sample['action'] == 'reuse':
            # Revalidate the bytes and metadata immediately before relying on this slot.
            meta = device.metadata(sample['slot'])
            if device.slot_pcm(sample['slot']) != sample['pcm'] or any(
                    meta.get(k) != v for k, v in {'name': sample['name'], **sample['fields']}.items()):
                raise invalid('Reused sample changed after preflight', observed=sample['slot'])
            entry['status'] = 'reused'
            journal.save(record)
            continue
        if device.slot_exists(sample['slot']) or len(sample['pcm']) >= device.sample_root().free_space_in_bytes:
            raise invalid('Upload destination or free memory changed', observed=sample['slot'])
        entry['status'] = 'upload_attempted'
        journal.save(record)
        device.upload_sample(sample['slot'], sample['name'], sample['pcm'])
        entry['status'] = 'metadata_attempted'
        journal.save(record)
        fields = {'name': sample['name'], **sample['fields']}
        device.set_metadata(sample['slot'], fields)
        device.begin_read()
        meta = device.metadata(sample['slot'])
        if not meta or meta.get('crc') != sample['crc'] or any(meta.get(k) != v for k, v in fields.items()) or device.slot_pcm(sample['slot']) != sample['pcm']:
            raise VerificationFailed('Restored audio or metadata differs', observed=sample['slot'], next_step='Inspect the slot and journal; project was not imported.')
        entry['status'] = 'restored'
        expected['slots'].add(sample['slot'])
        journal.save(record)
    fresh = snapshot(device)
    if any(fresh[k] != expected[k] for k in ('sku', 'os_version', 'serial', 'slots', 'pads')):
        raise VerificationFailed('Device changed during restore', next_step='Inspect the journal and take a fresh backup.')


class Restorer:
    def __init__(self, backups, journal):
        self.backups, self.journal = backups, journal
        self.confirmations = {}

    def restore(self, source, slots, backup_id, device, confirm=None):
        if not slots or any(type(s) is not int or not 1 <= s <= 999 for s in slots) or len(set(slots)) != len(slots):
            raise invalid('slots must be distinct integers 1..999')
        raw = Path(source).expanduser().read_bytes()
        meta, _, sounds = read_pak(raw)
        live = self.backups.require_current(backup_id, device)
        if meta.get('device_sku') != live['sku']:
            raise invalid('Backup SKU does not match device')
        indexed = sound_index(sounds)
        if any(s not in indexed for s in slots):
            raise invalid('Requested slot missing from source')
        selected = {indexed[s][0]: indexed[s][2] for s in slots}
        plan = plan_sounds(selected, device)
        impact = sound_impact(plan, live)
        binding = hashlib.sha256(json.dumps({'impact': impact, 'backup': backup_id,
            'device': device_id(live), 'source': hashlib.sha256(raw).hexdigest()}, sort_keys=True).encode()).hexdigest()
        previous = self.confirmations.pop(confirm, None)
        if previous is None or previous[0] != binding or previous[1] < time.monotonic():
            self.confirmations = {k: v for k, v in self.confirmations.items() if v[1] >= time.monotonic()}
            token = secrets.token_urlsafe(32)
            self.confirmations[token] = (binding, time.monotonic() + 300)
            return {'status': 'needs_confirmation', 'impact': impact, 'confirm': token,
                    'instruction': 'Approve this exact slot and reference impact before repeating.'}
        record = self.journal.create(device_id(live), backup_id, [], operation='restore_samples')
        record['sounds'] = [e | {'status': 'pending'} for e in impact]
        self.journal.save(record)
        self.backups.invalidate()
        try:
            upload_sounds(plan, device, self.journal, record, live)
            record['status'] = 'restored'
        except (DeviceError, OSError, ValueError) as e:
            record['status'] = 'partial'
            record['failure'] = {'error': type(e).__name__, 'message': str(e)}
        self.journal.save(record)
        return {'status': record['status'], 'journal_id': record['id'], 'sounds': record['sounds'],
                'failure': record.get('failure'), 'transactional': False, 'power_cycle_verified': False}
