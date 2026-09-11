"""TE SysEx framing for the EP-133.

Frame layout, verified on hardware (docs/research/upload-capture.md):

    F0 00 20 76 [identity] 40 [flags] [reqLo] [cmd] [packed payload] F7

flags = 0x40 (request) | 0x20 (request id present) | (req_id >> 7) & 0x1F
reqLo = req_id & 0x7F

Responses carry a status byte immediately after the command byte, then the
packed payload. Status 0 is success; 1 is a generic error whose payload is a
short ASCII message (e.g. "invalid file id").

Layout ported from ep133-ppak `ep133/sysex.py` (MIT) / phones24 `device.ts`.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .packing import pack, unpack

SYSEX_START = 0xF0
SYSEX_END = 0xF7
TE_ID = (0x00, 0x20, 0x76)
TE_MARKER = 0x40
FLAG_REQUEST = 0x40
FLAG_HAS_REQUEST_ID = 0x20

CMD_GREET = 0x01
CMD_FILE = 0x05

REQUEST_ID_MODULUS = 4096


class RequestIds:
    """Sequential 12-bit request ids from a random seed; the device echoes them."""

    def __init__(self, seed: int | None = None) -> None:
        self._value = (random.randint(0, REQUEST_ID_MODULUS - 2) if seed is None else seed) % REQUEST_ID_MODULUS

    def next(self) -> int:
        self._value = (self._value + 1) % REQUEST_ID_MODULUS
        return self._value


def build_frame(command: int, payload: bytes, request_id: int, identity: int = 0) -> bytes:
    if not 0 <= request_id < REQUEST_ID_MODULUS:
        raise ValueError(f"request_id {request_id} out of 12-bit range")
    if not 0 <= command < 0x80:
        raise ValueError(f"command {command} must fit in 7 bits")
    head = bytes([
        SYSEX_START, *TE_ID, identity, TE_MARKER,
        FLAG_REQUEST | FLAG_HAS_REQUEST_ID | ((request_id >> 7) & 0x1F),
        request_id & 0x7F,
        command,
    ])
    return head + pack(payload) + bytes([SYSEX_END])


@dataclass(frozen=True)
class Response:
    identity: int
    request_id: int
    command: int
    status: int
    payload: bytes

    @property
    def ok(self) -> bool:
        return self.status == 0


def parse_frame(frame: bytes) -> Response | None:
    """Parse a TE SysEx response. Returns None for anything that is not one."""
    if (
        len(frame) < 10
        or frame[0] != SYSEX_START
        or tuple(frame[1:4]) != TE_ID
        or frame[5] != TE_MARKER
        or frame[-1] != SYSEX_END
    ):
        return None
    flags = frame[6]
    if flags & FLAG_REQUEST:
        return None  # a request echoed back, not a response
    has_id = bool(flags & FLAG_HAS_REQUEST_ID)
    request_id = ((flags & 0x1F) << 7) | (frame[7] & 0x7F) if has_id else 0
    return Response(
        identity=frame[4],
        request_id=request_id,
        command=frame[8],
        status=frame[9],
        payload=unpack(frame[10:-1]),
    )
