"""Pattern, scene, settings and pad-record bytes for the fields proven in
docs/research/pattern-encoding.md. Encoders emit only what the device itself
writes; decoders exist so tests and the manifest can read the result back.

Note event (8 bytes): pos u16 LE | (pad-1)<<3 | type 0 | note | velocity | duration u16 LE | 0.
Byte 4 is velocity: 100 is what the device stores when it is not reading pad
pressure, and a pressure-sensitive recording on 2026-09-12 stored 127 for hard
hits and 54..71 for soft ones (docs/research/velocity-proof.md). 'x' encodes
100, 'o' encodes SOFT_VELOCITY. Byte 7 is 0 (a device-produced value).
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
DEFAULT_VELOCITY = 100           # what the device records without pad pressure
SOFT_VELOCITY = 60               # 'o': inside the 54..71 the device stored for soft hits
MAX_VELOCITY = 127
STEP_DURATION = TICKS_PER_STEP
MAX_DURATION = 0xFFFF
EVENT_TYPE_NOTE = 0
EVENT_TYPE_PARAM = 1
MAX_NOTE = 127
MAX_PARAM = 255
MAX_AUTOMATION_VALUE = 0x7FFF     # 15-bit: 0..32759 observed
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
    events: list[tuple[int, int, int]] = []
    for pad, row in steps.items():
        if type(pad) is not int or not 1 <= pad <= 12:
            raise ValueError(f"pad must be 1..12: {pad!r}")
        if not isinstance(row, str) or len(row) != length or set(row) - {HIT, SOFT_HIT, REST}:
            raise ValueError(f"steps for pad {pad} must be {length} characters of x, o or .")
        events.extend((i * TICKS_PER_STEP, pad, SOFT_VELOCITY if ch == SOFT_HIT else DEFAULT_VELOCITY)
                      for i, ch in enumerate(row) if ch != REST)
    if len(events) > MAX_EVENTS:
        raise ValueError(f"pattern has {len(events)} events; the header holds at most {MAX_EVENTS}")
    events.sort()
    out = bytearray([0, bars, len(events), 0])
    for position, pad, velocity in events:
        out += struct.pack("<HBBBHB", position, (pad - 1) << 3 | EVENT_TYPE_NOTE, NOTE, velocity, STEP_DURATION, 0)
    return bytes(out)


def encode_events(bars: int, events: list[tuple], automation: list[tuple[int, int, int]] = ()) -> bytes:
    """4-byte header plus one note event per (pad, tick, duration[, note[, velocity]]) and one
    parameter event per (tick, param, value), sorted by tick with notes before automation at the
    same tick.

    The device stores TICKS_PER_STEP ticks per 16th and its own recordings use all of them: a
    hand-played bar on this hardware sat a mean of 4 ticks (21 ms) off the 16ths, with 3 of 77
    events exactly on a step. Snapping a transcription to steps throws that away, so this is the
    encoder transcription uses; encode_pattern stays for patterns written as step strings.

    note defaults to 60, the value in 2315 of 3612 device events; other values (21..72 seen) sit on
    pads that carry pitched runs, and which pitch the device plays for them is unverified. velocity
    defaults to 100, the device's own value when it records without pad pressure; a pressure
    recording stored 127 for hard hits and 54..71 for soft ones, so 1..127 is the range. A
    parameter event is `pos | 0x01 | param | 0x00 | value u16 | 0`, the shape of the 66 recorded
    fader moves in the 2026-09-09 backup (ids 1, 5, 6; values 0..32759); the ids' meanings are
    unmapped, so the caller names a number and the device decides."""
    if type(bars) is not int or not 1 <= bars <= MAX_BARS:
        raise ValueError(f"bars must be 1..{MAX_BARS}")
    limit = bars * STEPS_PER_BAR * TICKS_PER_STEP
    placed: list[tuple[int, int, bytes]] = []
    for item in events:
        pad, tick, duration = item[:3]
        note = item[3] if len(item) > 3 else NOTE
        velocity = item[4] if len(item) > 4 else DEFAULT_VELOCITY
        if type(pad) is not int or not 1 <= pad <= 12:
            raise ValueError(f"pad must be 1..12: {pad!r}")
        if type(tick) is not int or not 0 <= tick < limit:
            raise ValueError(f"tick must be 0..{limit - 1} for a {bars}-bar pattern: {tick!r}")
        if type(duration) is not int or not 1 <= duration <= MAX_DURATION:
            raise ValueError(f"duration must be 1..{MAX_DURATION} ticks: {duration!r}")
        if type(note) is not int or not 0 <= note <= MAX_NOTE:
            raise ValueError(f"note must be 0..{MAX_NOTE}: {note!r}")
        if type(velocity) is not int or not 1 <= velocity <= MAX_VELOCITY:
            raise ValueError(f"velocity must be 1..{MAX_VELOCITY}: {velocity!r}")
        placed.append((tick, EVENT_TYPE_NOTE, struct.pack("<HBBBHB", tick, (pad - 1) << 3 | EVENT_TYPE_NOTE, note,
                                                          velocity, duration, 0)))
    for tick, param, value in automation:
        if type(tick) is not int or not 0 <= tick < limit:
            raise ValueError(f"automation tick must be 0..{limit - 1} for a {bars}-bar pattern: {tick!r}")
        if type(param) is not int or not 0 <= param <= MAX_PARAM:
            raise ValueError(f"automation param must be 0..{MAX_PARAM}: {param!r}")
        if type(value) is not int or not 0 <= value <= MAX_AUTOMATION_VALUE:
            raise ValueError(f"automation value must be 0..{MAX_AUTOMATION_VALUE}: {value!r}")
        placed.append((tick, EVENT_TYPE_PARAM, struct.pack("<HBBBHB", tick, EVENT_TYPE_PARAM, param, 0, value, 0)))
    if len(placed) > MAX_EVENTS:
        raise ValueError(f"pattern has {len(placed)} events; the header holds at most {MAX_EVENTS}")
    placed.sort(key=lambda item: item[:2])
    out = bytearray([0, bars, len(placed), 0])
    for _, _, raw in placed:
        out += raw
    return bytes(out)


def decode_pattern(data: bytes) -> dict:
    """{bars, events: [{pos, pad, type, note, velocity, duration, byte7}]} for any device pattern file."""
    if len(data) < PATTERN_HEADER or (len(data) - PATTERN_HEADER) % EVENT_SIZE:
        raise ValueError("pattern size is not 4+8n")
    bars, count = data[1], data[2]
    events = []
    for off in range(PATTERN_HEADER, len(data), EVENT_SIZE):
        pos, b2, note, velocity, duration, b7 = struct.unpack_from("<HBBBHB", data, off)
        events.append({"pos": pos, "pad": (b2 >> 3) + 1, "type": b2 & 7, "note": note, "velocity": velocity,
                       "duration": duration, "byte7": b7})
    if count != len(events):
        raise ValueError(f"header count {count} != {len(events)} events")
    return {"bars": bars, "events": events}


def pattern_steps(data: bytes, strict: bool = True) -> dict[int, str]:
    """x/. rows per pad on the 16th grid.

    strict (the default) raises on an event that is not on a step, which is what a pattern this
    code wrote must look like. Patterns the device recorded are almost never on the grid - the
    owner's hand-played bar had 3 of 77 events on a step - so pass strict=False to read one as
    rows, rounding each event to its nearest step. Rounding loses the micro-timing: use
    pattern_events when it matters."""
    decoded = decode_pattern(data)
    length = decoded["bars"] * STEPS_PER_BAR
    rows = {}
    for e in decoded["events"]:
        if e["type"] != EVENT_TYPE_NOTE:
            continue                      # fader automation has no step; pattern_automation lists it
        if strict and (e["pos"] % TICKS_PER_STEP or e["pos"] >= length * TICKS_PER_STEP):
            raise ValueError(f"event at tick {e['pos']} is off the 16th grid")
        row = rows.setdefault(e["pad"], [REST] * length)
        row[min(length - 1, round(e["pos"] / TICKS_PER_STEP))] = HIT
    return {pad: "".join(row) for pad, row in rows.items()}


def pattern_events(data: bytes) -> list[dict]:
    """Note events as {pad, tick, duration, note, velocity}, keeping the tick the device stored."""
    return [{"pad": e["pad"], "tick": e["pos"], "duration": e["duration"], "note": e["note"],
             "velocity": e["velocity"]}
            for e in decode_pattern(data)["events"] if e["type"] == EVENT_TYPE_NOTE]


def pattern_automation(data: bytes) -> list[dict]:
    """Parameter events as {tick, param, value}; for a type-1 event byte 3 is the id and 5-6 the value."""
    return [{"tick": e["pos"], "param": e["note"], "value": e["duration"]}
            for e in decode_pattern(data)["events"] if e["type"] == EVENT_TYPE_PARAM]


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


SETTINGS_PARAMS_OFFSET = 24
SETTINGS_PARAMS = 48
SETTINGS_GROUP_BYTES = 216
FX_SELECTOR_OFFSET = 4
FX_PARAMS_OFFSET = 8
FX_PARAMS = 34
FX_SIZES = (144, 160)
KNOB_STEP = 256                  # every device-written knob float is n/256


def _knob(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
        raise ValueError("knob values are 0.0..1.0")
    return round(value * KNOB_STEP) / KNOB_STEP


def patch_settings_params(settings: bytes, params: dict[int, float], group_bytes: dict[str, int]) -> bytes:
    """Raw patches to settings floats (index 0..47, snapped to n/256) and the four group bytes.

    Meanings are the hypotheses in docs/research/fx-and-settings-map.md (params[12*g + f] = fader
    function f of group g; group byte = assigned function); this only places bytes."""
    if len(settings) not in SETTINGS_SIZES:
        raise ValueError(f"settings file must be one of {SETTINGS_SIZES} bytes")
    out = bytearray(settings)
    for index, value in params.items():
        if type(index) is not int or not 0 <= index < SETTINGS_PARAMS:
            raise ValueError(f"settings param index must be 0..{SETTINGS_PARAMS - 1}: {index!r}")
        struct.pack_into("<f", out, SETTINGS_PARAMS_OFFSET + 4 * index, _knob(value))
    for group, value in group_bytes.items():
        if group not in GROUPS or type(value) is not int or not 0 <= value <= 255:
            raise ValueError(f"group byte must be group A..D and 0..255: {group!r}={value!r}")
        out[SETTINGS_GROUP_BYTES + GROUPS.index(group)] = value
    return bytes(out)


def patch_fx_settings(fx: bytes, selector: int | None, params: dict[int, float]) -> bytes:
    """Raw patches to the effect selector byte and the 34 knob floats (snapped to n/256)."""
    if len(fx) not in FX_SIZES:
        raise ValueError(f"fx_settings file must be one of {FX_SIZES} bytes")
    out = bytearray(fx)
    if selector is not None:
        if type(selector) is not int or not 0 <= selector <= 255:
            raise ValueError("fx selector must be 0..255")
        out[FX_SELECTOR_OFFSET] = selector
    for index, value in params.items():
        if type(index) is not int or not 0 <= index < FX_PARAMS:
            raise ValueError(f"fx param index must be 0..{FX_PARAMS - 1}: {index!r}")
        struct.pack_into("<f", out, FX_PARAMS_OFFSET + 4 * index, _knob(value))
    return bytes(out)


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
    merged += [(pos, 1, i, struct.pack("<HBBBHB", pos, (pad - 1) << 3 | EVENT_TYPE_NOTE, NOTE, DEFAULT_VELOCITY,
                                       STEP_DURATION, 0)) for i, (pos, pad) in enumerate(sorted(new))]
    merged.sort(key=lambda item: item[:3])
    return bytes([data[0], decoded["bars"], len(merged), data[3]]) + b"".join(raw for *_, raw in merged)
