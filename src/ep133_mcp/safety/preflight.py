"""Pure preflight decisions over fresh device snapshots and validated PCM."""

from dataclasses import dataclass
from pathlib import Path
import wave
import zlib

from ..protocol.payloads import MAX_UPLOAD_BYTES, pad_node
from .errors import InvalidDestination, NoSafeSlot, TooLarge, UnsupportedFormat

RATE = 46875
BYTES_PER_SECOND = RATE * 2
MAX_PCM_BYTES = MAX_UPLOAD_BYTES


@dataclass(frozen=True)
class Sample:
    name: str
    pcm: bytes
    frames: int
    crc: int

    @property
    def duration_seconds(self):
        return self.frames / RATE


def read_sample(path: str | Path) -> Sample:
    path = Path(path).expanduser()
    try:
        with wave.open(str(path), 'rb') as wav:
            observed = {'channels': wav.getnchannels(), 'bits': wav.getsampwidth() * 8,
                        'rate': wav.getframerate(), 'compression': wav.getcomptype()}
            if observed != {'channels': 1, 'bits': 16, 'rate': RATE, 'compression': 'NONE'}:
                raise UnsupportedFormat('Unsupported WAV format', observed=observed,
                                        expected='46875 Hz, mono, 16-bit PCM WAV',
                                        next_step='Export this format explicitly; no automatic resampling is performed.')
            frames = wav.getnframes()
            if frames <= 0:
                raise ValueError('sample contains no frames')
            if frames * 2 > MAX_PCM_BYTES:
                raise TooLarge('Sample exceeds the upload page limit', observed=frames * 2,
                               expected=f'At most {MAX_PCM_BYTES} PCM bytes',
                               next_step='Trim the sample before importing.')
            pcm = wav.readframes(frames)
            if len(pcm) != frames * 2:
                raise ValueError('truncated PCM data')
    except (OSError, EOFError, wave.Error, ValueError) as e:
        raise UnsupportedFormat('WAV could not be read completely', observed=str(e),
                                expected='Readable nonempty PCM WAV',
                                next_step='Check the path and export a complete WAV file.') from e
    # Fixed ASCII name avoids unverified device filename encodings/lengths.
    return Sample('mcp_sample', pcm, frames, zlib.crc32(pcm))


def validate_destination(project, group, pad):
    try:
        if type(project) is not int or type(pad) is not int or not isinstance(group, str):
            raise ValueError('project and pad must be integers; group must be a letter')
        return pad_node(project, group, pad)
    except ValueError as e:
        raise InvalidDestination('Invalid destination', observed=str(e),
                                 expected='project 1..9, group A..D, pad index 1..12',
                                 next_step='Use list_pads to identify the intended pad index.') from e


def prepare(samples: list[tuple[int, Sample]], project: int, group: str,
            live: dict, free_bytes: int) -> list[dict]:
    if not 1 <= len(samples) <= 12 or len({pad for pad, _ in samples}) != len(samples):
        raise InvalidDestination('Kit must contain 1..12 distinct pads',
                                 next_step='Provide each pad once.')
    for pad, _ in samples:
        validate_destination(project, group, pad)
    required = sum(len(sample.pcm) for _, sample in samples)
    if type(free_bytes) is not int or free_bytes < 0:
        raise TooLarge('Free memory could not be established', observed=free_bytes,
                       next_step='Reconnect and re-read device memory.')
    if required >= free_bytes:
        error = TooLarge('PCM does not fit in current free memory', observed=required,
                         expected=f'Fewer than {free_bytes} bytes',
                         next_step='Trim samples or free memory with Sample Tool, then make a fresh backup.')
        error.detail.update(free_bytes=free_bytes, required_pcm_bytes=required,
                            maximum_duration_seconds=max(0, (free_bytes - 1) // 2) / RATE)
        raise error
    # Include zero-length records: populating their stored slot is also an impact.
    referenced = {slot for slot, _ in live['pads'].values()}
    available = [slot for slot in range(1, 1000)
                 if slot not in live['slots'] and slot not in referenced]
    if len(available) < len(samples):
        raise NoSafeSlot('No unoccupied, unreferenced library slots available',
                         observed=len(available), expected=len(samples),
                         next_step='Review stale references in Sample Tool and create a fresh backup.')
    plan = []
    for (pad, sample), slot in zip(samples, available):
        old_slot, old_length = live['pads'][project, group, pad]
        plan.append({'project': project, 'group': group, 'pad': pad,
                     'node': validate_destination(project, group, pad), 'slot': slot,
                     'prior_slot': old_slot, 'prior_length': old_length,
                     'destructive': old_length != 0 or old_slot in live['slots'],
                     'sample': sample})
    return plan
