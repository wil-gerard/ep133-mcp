"""Pattern, scene, settings and pad-record bytes for the fields proven in
docs/research/pattern-encoding.md. Encoders emit only what the device itself
writes; decoders exist so tests and the manifest can read the result back.

Note event (8 bytes): pos u16 LE | (pad-1)<<3 | type 0 | note | 100 | duration u16 LE | 0.
Byte 4 is 100 in every device event (velocity unproven), byte 7 is 0 (a
device-produced value). 'x' and 'o' both encode the same event until velocity
is proven, and the generator says so in its response.
"""

from __future__ import annotations

import struct

TICKS_PER_BAR = 384
STEPS_PER_BAR = 16
TICKS_PER_STEP = TICKS_PER_BAR // STEPS_PER_BAR
EVENT_SIZE = 8
PATTERN_HEADER = 4
MAX_EVENTS = 255                 # header count is one byte
MAX_BARS = 15                    # highest device-written value
MAX_PATTERN_INDEX = 99
NOTE = 60
VELOCITY_BYTE = 100
STEP_DURATION = TICKS_PER_STEP
EVENT_TYPE_NOTE = 0
SCENES_HEAD = 7
SCENE_CHUNK = 6
SCENE_SLOTS = 99
SCENES_SIZES = (612, 712)
SETTINGS_SIZES = (220, 222, 224)
BPM_OFFSET = 4
PAD_RECORD_SIZES = (26, 27)
GROUPS = ("A", "B", "C", "D")
HIT, SOFT_HIT, REST = "x", "o", "."


def pattern_member(group: str, index: int) -> str:
    if group not in GROUPS or type(index) is not int or not 1 <= index <= MAX_PATTERN_INDEX:
        raise ValueError(f"pattern must be group A..D and index 1..{MAX_PATTERN_INDEX}: {group}{index}")
    return f"patterns/{group.lower()}{index:02d}"


def encode_pattern(bars: int, steps: dict[int, str]) -> bytes:
    """4-byte header plus one note event per 'x'/'o' step, sorted by position (pad order within a tick)."""
    if type(bars) is not int or not 1 <= bars <= MAX_BARS:
        raise ValueError(f"bars must be 1..{MAX_BARS}")
    length = bars * STEPS_PER_BAR
    events: list[tuple[int, int]] = []
    for pad, row in steps.items():
        if type(pad) is not int or not 1 <= pad <= 12:
            raise ValueError(f"pad must be 1..12: {pad!r}")
        if not isinstance(row, str) or len(row) != length or set(row) - {HIT, SOFT_HIT, REST}:
            raise ValueError(f"steps for pad {pad} must be {length} characters of x, o or .")
        events.extend((i * TICKS_PER_STEP, pad) for i, ch in enumerate(row) if ch != REST)
    if len(events) > MAX_EVENTS:
        raise ValueError(f"pattern has {len(events)} events; the header holds at most {MAX_EVENTS}")
    events.sort()
    out = bytearray([0, bars, len(events), 0])
    for position, pad in events:
        out += struct.pack("<HBBBHB", position, (pad - 1) << 3 | EVENT_TYPE_NOTE, NOTE, VELOCITY_BYTE, STEP_DURATION, 0)
    return bytes(out)


def decode_pattern(data: bytes) -> dict:
    """{bars, events: [{pos, pad, type, note, byte4, duration, byte7}]} for any device pattern file."""
    if len(data) < PATTERN_HEADER or (len(data) - PATTERN_HEADER) % EVENT_SIZE:
        raise ValueError("pattern size is not 4+8n")
    bars, count = data[1], data[2]
    events = []
    for off in range(PATTERN_HEADER, len(data), EVENT_SIZE):
        pos, b2, note, b4, duration, b7 = struct.unpack_from("<HBBBHB", data, off)
        events.append({"pos": pos, "pad": (b2 >> 3) + 1, "type": b2 & 7, "note": note, "byte4": b4,
                       "duration": duration, "byte7": b7})
    if count != len(events):
        raise ValueError(f"header count {count} != {len(events)} events")
    return {"bars": bars, "events": events}


def pattern_steps(data: bytes) -> dict[int, str]:
    """x/. rows per pad from a note-only pattern on the 16th grid; off-grid or non-note events raise."""
    decoded = decode_pattern(data)
    length = decoded["bars"] * STEPS_PER_BAR
    rows = {}
    for e in decoded["events"]:
        if e["type"] != EVENT_TYPE_NOTE:
            raise ValueError("pattern holds parameter events")
        if e["pos"] % TICKS_PER_STEP or e["pos"] >= length * TICKS_PER_STEP:
            raise ValueError(f"event at tick {e['pos']} is off the 16th grid")
        row = rows.setdefault(e["pad"], [REST] * length)
        row[e["pos"] // TICKS_PER_STEP] = HIT
    return {pad: "".join(row) for pad, row in rows.items()}


def scene_chunk_offset(scene: int) -> int:
    if type(scene) is not int or not 1 <= scene <= SCENE_SLOTS:
        raise ValueError(f"scene must be 1..{SCENE_SLOTS}")
    return SCENES_HEAD + SCENE_CHUNK * (scene - 1)


def patch_scene(scenes: bytes, scene: int, indexes: dict[str, int]) -> bytes:
    """Write one 6-byte chunk [a, b, c, d, 4, 4]. Every group needs an index 1..99: the device never
    writes 0 in a populated chunk, and a missing pattern file is its own representation of silence."""
    if len(scenes) not in SCENES_SIZES:
        raise ValueError(f"scenes file must be one of {SCENES_SIZES} bytes")
    if set(indexes) != set(GROUPS):
        raise ValueError("scene needs a pattern index for each of A, B, C, D")
    values = []
    for group in GROUPS:
        index = indexes[group]
        if type(index) is not int or not 1 <= index <= MAX_PATTERN_INDEX:
            raise ValueError(f"scene pattern index for group {group} must be 1..{MAX_PATTERN_INDEX}")
        values.append(index)
    off = scene_chunk_offset(scene)
    return scenes[:off] + bytes(values + [4, 4]) + scenes[off + SCENE_CHUNK:]


def decode_scene(scenes: bytes, scene: int) -> dict:
    off = scene_chunk_offset(scene)
    chunk = scenes[off:off + SCENE_CHUNK]
    return {"A": chunk[0], "B": chunk[1], "C": chunk[2], "D": chunk[3], "num": chunk[4], "den": chunk[5]}


def patch_bpm(settings: bytes, bpm: float) -> bytes:
    if len(settings) not in SETTINGS_SIZES:
        raise ValueError(f"settings file must be one of {SETTINGS_SIZES} bytes")
    if not isinstance(bpm, (int, float)) or isinstance(bpm, bool) or not 20.0 <= bpm <= 400.0:
        raise ValueError("bpm must be a number from 20 to 400")
    return settings[:BPM_OFFSET] + struct.pack("<f", float(bpm)) + settings[BPM_OFFSET + 4:]


def decode_bpm(settings: bytes) -> float:
    return struct.unpack_from("<f", settings, BPM_OFFSET)[0]


def patch_pad_record(record: bytes, slot: int, frames: int) -> bytes:
    """Stored slot (u16 LE at 1) and length in frames (u32 LE at 8); every other byte kept."""
    if len(record) not in PAD_RECORD_SIZES:
        raise ValueError(f"pad record must be one of {PAD_RECORD_SIZES} bytes")
    if type(slot) is not int or not 1 <= slot <= 999 or type(frames) is not int or frames <= 0:
        raise ValueError("pad needs slot 1..999 and frames > 0")
    out = bytearray(record)
    struct.pack_into("<H", out, 1, slot)
    struct.pack_into("<I", out, 8, frames)
    return bytes(out)


def add_events(data: bytes, hits: list[tuple[int, int]]) -> bytes:
    """Append note events at (pad, step) to an existing pattern file, keeping every existing byte.

    New events are inserted after any existing event at the same tick (recording order) and
    the header count is bumped; bars and all other bytes are untouched."""
    decoded = decode_pattern(data)
    length = decoded["bars"] * STEPS_PER_BAR
    new = []
    for pad, step in hits:
        if type(pad) is not int or not 1 <= pad <= 12:
            raise ValueError(f"pad must be 1..12: {pad!r}")
        if type(step) is not int or not 0 <= step < length:
            raise ValueError(f"step must be 0..{length - 1} for a {decoded['bars']}-bar pattern: {step!r}")
        new.append((step * TICKS_PER_STEP, pad))
    if len(decoded["events"]) + len(new) > MAX_EVENTS:
        raise ValueError(f"pattern would hold more than {MAX_EVENTS} events")
    existing = [data[off:off + EVENT_SIZE] for off in range(PATTERN_HEADER, len(data), EVENT_SIZE)]
    merged = [(struct.unpack_from("<H", raw)[0], 0, i, raw) for i, raw in enumerate(existing)]
    merged += [(pos, 1, i, struct.pack("<HBBBHB", pos, (pad - 1) << 3 | EVENT_TYPE_NOTE, NOTE, VELOCITY_BYTE,
                                       STEP_DURATION, 0)) for i, (pos, pad) in enumerate(sorted(new))]
    merged.sort(key=lambda item: item[:3])
    return bytes([data[0], decoded["bars"], len(merged), data[3]]) + b"".join(raw for *_, raw in merged)
