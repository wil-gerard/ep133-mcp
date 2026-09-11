"""Project reads verified in docs/research/project-tar-read.md. No file writes."""

import io
import struct
import tarfile

from .payloads import PAD_LABELS

PAGE_DATA_BYTES = 324
MAX_PROJECT_PAGES = 4096


def project_open(project: int) -> bytes:
    if type(project) is not int or not 1 <= project <= 9:
        raise ValueError("project must be an integer from 1 to 9")
    return struct.pack(">BBHI", 3, 0, 2000 + project * 1000, 0)


def project_page(page: int) -> bytes:
    if not 0 <= page < MAX_PROJECT_PAGES:
        raise ValueError("project page out of range")
    return struct.pack(">BBH", 3, 1, page)


def page_data(payload: bytes, page: int) -> bytes:
    # Response.status has already consumed the first byte of upstream's header.
    if not 2 <= len(payload) <= PAGE_DATA_BYTES + 2:
        raise ValueError("invalid project page length")
    if int.from_bytes(payload[:2], "big") != page:
        raise ValueError("project page index mismatch")
    return payload[2:]


def stored_pads(data: bytes) -> list[dict]:
    """Require every pad record; missing or malformed data is never empty."""
    if len(data) % 512 or not data.endswith(bytes(1024)):
        raise ValueError("incomplete project TAR")
    pads = []
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
        members = archive.getmembers()
        for group in "ABCD":
            for pad in range(1, 13):
                name = f"pads/{group.lower()}/p{pad:02}"
                matches = [m for m in members if m.name == name]
                if len(matches) != 1 or not matches[0].isfile():
                    raise ValueError(f"missing, duplicate or non-file pad: {name}")
                member = matches[0]
                if member.size not in (26, 27):
                    raise ValueError(f"unrecognized pad record size: {name}: {member.size}")
                record = archive.extractfile(member).read()
                pads.append({
                    "group": group, "pad": pad, "label": PAD_LABELS[pad],
                    "stored_slot": struct.unpack_from("<H", record, 1)[0],
                    "stored_length": struct.unpack_from("<I", record, 8)[0],
                })
    return pads
