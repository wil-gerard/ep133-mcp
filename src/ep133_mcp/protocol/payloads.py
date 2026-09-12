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
FILE_DELETE = 0x06
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


def file_delete(file_id: int) -> bytes:
    """`06 <file_id u16 BE>` - what Sample Tool sends.

    ep133-krate's `captures/sniffer-delete-hi.bin` (Sample Tool deleting slot 467 on OS 2.0.5)
    carries exactly `06 01 D3` straight after GREET, no FILE_INIT, answered status 0 and
    followed by the device's own free-space notifications as the audio is released. Upstream
    PROTOCOL.md's `06 02 <fid>` was never verified there and is rejected here: with the extra
    byte the device reads the id from the wrong offset (704 became 514, 30 became 512) and
    answers "failed to delete" (docs/research/delete-proof.md)."""
    _check_u16(file_id, "file_id")
    return struct.pack(">BH", FILE_DELETE, file_id)


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


FILE_LIST = 0x04
FILE_INFO = 0x0B
FLAG_FILE = 0x01     # phones24: entry.flags & 1 means file, else folder


def file_list(node: int, page: int = 0) -> bytes:
    """`04 <page u16 BE> <node u16 BE>`: one page of a directory node's children.

    ep133-ppak documents page 0 of this as GROUP_DUMP (safe); phones24's ep133-export-to-daw walks
    the whole filesystem with it from node 0, paging until an empty page. Neither is a read that
    leaves a file open."""
    _check_u16(node, "node")
    _check_u16(page, "page")
    return struct.pack(">BHH", FILE_LIST, page, node)


def file_info(file_id: int) -> bytes:
    """`0B <file_id u16 BE>`: STAT - node, parent, flags, size and name. Documented safe for any id."""
    _check_u16(file_id, "file_id")
    return struct.pack(">BH", FILE_INFO, file_id)


def _c_string(data: bytes, offset: int) -> tuple[str, int]:
    end = data.find(b"\0", offset)
    if end < 0:
        raise ValueError("unterminated name")
    return data[offset:end].decode("utf-8", "replace"), end + 1


def parse_file_list(payload: bytes, page: int) -> list[dict]:
    """Entries of one FILE_LIST page: [{node, flags, size, name, kind}]. Empty list = past the end."""
    if len(payload) < 2:
        return []
    if int.from_bytes(payload[:2], "big") != page:
        raise ValueError("file list page index mismatch")
    entries, offset = [], 2
    while offset < len(payload):
        if len(payload) - offset < 8:
            raise ValueError("truncated file list entry")
        node, flags, size = struct.unpack_from(">HBI", payload, offset)
        name, offset = _c_string(payload, offset + 7)
        entries.append({"node": node, "flags": flags, "size": size, "name": name,
                        "kind": "file" if flags & FLAG_FILE else "folder"})
    return entries


def parse_file_info(payload: bytes) -> dict:
    """STAT response: {node, parent, flags, size, name, kind}."""
    if len(payload) < 10:
        raise ValueError("file info response too short")
    node, parent, flags, size = struct.unpack_from(">HHBI", payload, 0)
    name, _ = _c_string(payload, 9)
    return {"node": node, "parent": parent, "flags": flags, "size": size, "name": name,
            "kind": "file" if flags & FLAG_FILE else "folder"}


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


# Ported from ep133-ppak (MIT); reproduced against the slot16 capture.
UPLOAD_CHUNK_BYTES = 433
MAX_UPLOAD_BYTES = UPLOAD_CHUNK_BYTES * 65535  # leave u16 page for terminator


def file_put_meta(name: str, data_size: int, slot: int) -> bytes:
    name_bytes = name.encode('ascii')
    if not name_bytes or b'\0' in name_bytes or not 1 <= slot <= 999:
        raise ValueError('invalid upload name or slot')
    if not 0 < data_size <= MAX_UPLOAD_BYTES:
        raise ValueError('upload exceeds verified page range')
    return (b'\x02\x00\x05' + struct.pack('>HHI', slot, 1000, data_size)
            + name_bytes + b'\0{"channels":1}')


def file_put_project(project: int, data_size: int) -> bytes:
    """`02 00 05 <2000+1000*project u16> <2000 u16> <size u32> "NN" 00` - a whole project TAR.

    What Sample Tool's `uploadProjectArchive` sends (its bundle, SysExFilePutInitRequest: flags
    CAPABILITY_READ|FILE_TYPE_FILE = 5, fileId = the project's node, parentId = /projects, the
    two-digit name from `PNN.tar`, no metadata JSON), followed by the same FILE_PUT_DATA pages
    and empty terminator as a sample upload. The project node already exists, so this overwrites
    it in place. docs/research/project-write.md."""
    if type(project) is not int or not 1 <= project <= 9:
        raise ValueError('project must be 1..9')
    if not 0 < data_size <= MAX_UPLOAD_BYTES:
        raise ValueError('project TAR exceeds verified page range')
    return (b'\x02\x00\x05' + struct.pack('>HHI', 2000 + 1000 * project, PROJECT_ROOT, data_size)
            + f'{project:02d}'.encode('ascii') + b'\0')


def file_put_data(page: int, data: bytes) -> bytes:
    _check_u16(page, 'page')
    if len(data) > UPLOAD_CHUNK_BYTES:
        raise ValueError('upload chunk exceeds 433 bytes')
    return b'\x02\x01' + struct.pack('>H', page) + data
