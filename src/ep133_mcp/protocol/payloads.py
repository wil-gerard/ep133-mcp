"""Unpacked payload builders and parsers for the FILE command family.

Every byte sequence here was sent to or received from hardware and is recorded
in docs/research/. Nothing speculative is built here; in particular there is
no FILE_DELETE. Verified project reads live in projects.py.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass

# FILE sub-commands (byte 0 of the unpacked payload)
FILE_INIT = 0x01
FILE_METADATA = 0x07
FILE_METADATA_SET = 0x01
FILE_METADATA_GET = 0x02

READ_MODE = 0
WRITE_MODE = 1
DEFAULT_MAX_RESPONSE = 4 * 1024 * 1024

# Well-known file ids
SAMPLE_ROOT = 1000
PROJECT_ROOT = 2000


def file_init(mode: int, max_response: int = DEFAULT_MAX_RESPONSE) -> bytes:
    """`01 <mode> <max u32 BE>`. Observed: `01 00 00 40 00 00` for reads."""
    if mode not in (READ_MODE, WRITE_MODE):
        raise ValueError("mode must be READ_MODE or WRITE_MODE")
    return struct.pack(">BBI", FILE_INIT, mode, max_response)


def metadata_get(file_id: int, page: int = 0) -> bytes:
    """`07 02 <file_id u16 BE> <page u16 BE>`."""
    _check_u16(file_id, "file_id")
    _check_u16(page, "page")
    return struct.pack(">BBHH", FILE_METADATA, FILE_METADATA_GET, file_id, page)


def metadata_set(file_id: int, fields: dict) -> bytes:
    """`07 01 <file_id u16 BE> <json> 00`. Compact JSON, ASCII, null-terminated."""
    _check_u16(file_id, "file_id")
    body = json.dumps(fields, separators=(",", ":")).encode("ascii")
    if b"\0" in body:
        raise ValueError("metadata must not contain null bytes")
    return struct.pack(">BBH", FILE_METADATA, FILE_METADATA_SET, file_id) + body + b"\0"


def pad_node(project: int, group: str, pad_num: int) -> int:
    """Metadata node id for a pad.

    `2000 + 1000*project + 200 + 100*group_index + pad_num`, where pad_num is
    the visual position top-to-bottom / left-to-right (1="7", 7="1", 10=".").
    Verified on hardware: the same index is used by SysEx, metadata nodes and
    the project TAR (docs/research/phase0-proof.md).
    """
    if not 1 <= project <= 9:
        raise ValueError(f"project {project} must be 1..9")
    if group not in "ABCD" or len(group) != 1:
        raise ValueError(f"group {group!r} must be one of A, B, C, D")
    if not 1 <= pad_num <= 12:
        raise ValueError(f"pad_num {pad_num} must be 1..12")
    return 2000 + 1000 * project + 200 + 100 * "ABCD".index(group) + pad_num


PAD_LABELS = {1: "7", 2: "8", 3: "9", 4: "4", 5: "5", 6: "6",
              7: "1", 8: "2", 9: "3", 10: ".", 11: "0", 12: "ENTER"}
LABEL_TO_PAD = {v: k for k, v in PAD_LABELS.items()}


def project_base(node_or_active: int) -> int:
    """Project number from a node id or the project-root `active` value."""
    return (node_or_active - 2000) // 1000


@dataclass(frozen=True)
class Greeting:
    product: str
    mode: str
    sku: str
    base_sku: str
    os_version: str
    sw_version: str
    bl_version: str
    serial: str

    @classmethod
    def parse(cls, payload: bytes) -> "Greeting":
        text = payload.split(b"\0", 1)[0].decode("utf-8", "replace")
        fields = dict(item.split(":", 1) for item in text.split(";") if ":" in item)
        return cls(
            product=fields.get("product", ""),
            mode=fields.get("mode", ""),
            sku=fields.get("sku", ""),
            base_sku=fields.get("base_sku", ""),
            os_version=fields.get("os_version", ""),
            sw_version=fields.get("sw_version", ""),
            bl_version=fields.get("bl_version", ""),
            serial=fields.get("serial", ""),
        )


def parse_metadata_page(payload: bytes) -> bytes:
    """Strip the observed 2-byte prefix from a metadata GET page.

    Pages concatenate to a null-terminated JSON document; the caller walks pages
    until it sees the terminator. Occupied sample slots routinely exceed one page.
    """
    if len(payload) < 2:
        return b""
    return payload[2:]


def _check_u16(value: int, name: str) -> None:
    if not 0 <= value < 2**16:
        raise ValueError(f"{name} {value} must fit in u16")
