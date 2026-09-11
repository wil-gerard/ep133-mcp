"""7-bit MIDI-safe packing for SysEx payloads.

Ported from ep133-ppak `ep133/packing.py` (MIT), itself a port of
phones24/ep133-export-to-daw `utils.ts`. Byte-verified here against the
hardware capture in docs/research/fixtures/upload-capture-slot16.json.

Every group of up to 7 input bytes becomes one MSB byte (bit N carries the high
bit of input byte N) followed by the 7 inputs with their high bits cleared.
"""

from __future__ import annotations


def packed_length(raw_length: int) -> int:
    if raw_length <= 0:
        return 0
    full, tail = divmod(raw_length, 7)
    return full * 8 + (1 + tail if tail else 0)


def pack(data: bytes) -> bytes:
    out = bytearray(packed_length(len(data)))
    out_index = 1
    msb_index = 0
    for i, byte in enumerate(data):
        pos = i % 7
        out[msb_index] |= (byte >> 7) << pos
        out[out_index] = byte & 0x7F
        out_index += 1
        if pos == 6 and i < len(data) - 1:
            msb_index += 8
            out_index += 1
    return bytes(out)


def unpack(packed: bytes) -> bytes:
    if not packed:
        return b""
    out = bytearray()
    msb_index = 0
    bit = 0
    read = 1
    msb_byte = packed[msb_index]
    while read < len(packed):
        out.append(((msb_byte >> bit) & 1) << 7 | (packed[read] & 0x7F))
        bit += 1
        read += 1
        if bit > 6:
            read += 1
            bit = 0
            msb_index += 8
            if msb_index >= len(packed):
                break
            msb_byte = packed[msb_index]
    return bytes(out)
