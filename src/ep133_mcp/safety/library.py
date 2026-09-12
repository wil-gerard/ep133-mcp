"""Portable project exports and complete stored-reference inventory."""

from pathlib import Path
import hashlib
import io
import wave

from ..protocol.projects import build_ppak, project_meta, read_pak, stored_pads
from .backup import LIBRARY_SLOTS
from .capture import SOUND_NAME
from .errors import InvalidDestination


def sound_index(sounds):
    indexed = {}
    for entry, data in sounds.items():
        match = SOUND_NAME.fullmatch(entry)
        if not match or not 1 <= int(match[1]) <= 999 or int(match[1]) in indexed:
            raise InvalidDestination(next_step='Check the source backup and requested destination.', 'Invalid or duplicate sound slot', observed=entry)
        indexed[int(match[1])] = (entry, match[2], data)
    return indexed


def references(projects):
    usage = {}
    for project, tar in sorted(projects.items()):
        for pad in stored_pads(tar):
            if pad['stored_slot']:
                usage.setdefault(pad['stored_slot'], []).append({'project': project, **pad})
    return usage


def list_samples(device=None, source=None):
    if source:
        _, projects, sounds = read_pak(Path(source).expanduser().read_bytes())
        if sorted(projects) != list(range(1, 10)):
            raise InvalidDestination(next_step='Check the source backup and requested destination.', 'Usage requires a full nine-project backup')
        indexed = sound_index(sounds)
        metadata = {}
        for slot, (_, name, data) in indexed.items():
            with wave.open(io.BytesIO(data)) as wav:
                frames, channels, rate = wav.getnframes(), wav.getnchannels(), wav.getframerate()
                pcm = wav.readframes(frames)
                if len(pcm) != frames * channels * wav.getsampwidth():
                    raise InvalidDestination(next_step='Check the source backup and requested destination.', 'Truncated WAV', observed=slot)
                metadata[slot] = {'name': name, 'frames': frames, 'channels': channels,
                                  'samplerate': rate, 'pcm_bytes': len(pcm)}
    else:
        device.greet()
        device.begin_read()
        projects = {p: device.project_tar(p) for p in range(1, 10)}
        device.begin_read()
        metadata = {}
        for slot in LIBRARY_SLOTS:
            if device.slot_exists(slot):
                meta = device.metadata(slot)
                if not meta or '_unparsed' in meta:
                    raise InvalidDestination(next_step='Check the source backup and requested destination.', 'Sample metadata unavailable', observed=slot)
                metadata[slot] = meta
    usage = references(projects)
    entries = [{'slot': slot, **meta, 'references': usage.get(slot, []),
                'unreferenced': slot not in usage} for slot, meta in sorted(metadata.items())]
    return {'samples': entries, 'count': len(entries), 'projects_scanned': 9,
            'unreferenced_slots': [e['slot'] for e in entries if e['unreferenced']],
            'stale_references': {s: refs for s, refs in usage.items() if s not in metadata},
            'source': str(source) if source else 'device'}


def export_project(source, project, out, include_samples=True):
    """Export a project from a backup; WAV containers and project bytes stay intact."""
    if type(project) is not int or not 1 <= project <= 9:
        raise InvalidDestination(next_step='Check the source backup and requested destination.', 'project must be 1..9')
    out = Path(out).expanduser()
    if out.suffix != '.ppak':
        raise InvalidDestination(next_step='Check the source backup and requested destination.', 'Output must end in .ppak')
    meta, projects, sounds = read_pak(Path(source).expanduser().read_bytes())
    if project not in projects:
        raise InvalidDestination(next_step='Check the source backup and requested destination.', 'Project absent from source', observed=project)
    tar = projects[project]
    slots = sorted(references({project: tar}))
    indexed = sound_index(sounds)
    missing = [s for s in slots if s not in indexed]
    if include_samples and missing:
        raise InvalidDestination(next_step='Check the source backup and requested destination.', 'Referenced samples absent from backup', observed=missing)
    selected = {indexed[s][0]: indexed[s][2] for s in slots} if include_samples else {}
    data = build_ppak(project, tar, project_meta(meta), selected)
    with out.open('xb') as stream:
        stream.write(data)
    return {'status': 'exported', 'path': str(out), 'project': project, 'referenced_slots': slots,
            'included_slots': slots if include_samples else [], 'missing_slots': missing,
            'bytes': len(data), 'sound_bytes': sum(map(len, selected.values())),
            'sha256': hashlib.sha256(data).hexdigest()}
