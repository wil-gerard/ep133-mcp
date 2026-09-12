"""Project TAR reads (verified in docs/research/project-tar-read.md) and the
device-flavour TAR/.ppak packers (docs/handoff/pattern-encoding-next.md rung 2).

Everything here builds bytes; nothing writes files or opens a port.

The device writes old V7 tar headers: NUL-padded name, mode as seven octal
digits plus NUL, uid/gid/mtime fields entirely NUL, size as bare octal digits
NUL-padded (all NUL for directories), checksum as bare octal digits, a NUL
and spaces, typeflag '0'/'5', no ustar magic, members in plain byte order
with a directory entry for every parent, and exactly two zero blocks at the
end with no record padding. pack_project reproduces that byte for byte, which
tests prove against every project in a real backup.
"""

import io
import json
import struct
import tarfile
import time
import zipfile

from .payloads import PAD_LABELS

PAGE_DATA_BYTES = 324
MAX_PROJECT_PAGES = 4096
TAR_BLOCK = 512
FILE_MODE = 0o644
DIR_MODE = 0o755
PAK_INFO = "teenage engineering - pak file"
PAK_RELEASE = "1.2.0"
META_KEYS = ("info", "pak_version", "pak_type", "pak_release", "device_name", "device_sku",
             "device_version", "generated_at", "author", "base_sku")


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


# --- device-flavour TAR ---------------------------------------------------------

def unpack_project(data: bytes) -> dict[str, bytes]:
    """{member name: bytes} for every regular file in a project TAR, in archive order."""
    if len(data) % TAR_BLOCK or not data.endswith(bytes(2 * TAR_BLOCK)):
        raise ValueError("incomplete project TAR")
    files: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
        for member in archive.getmembers():
            if member.name.startswith(("/", "../")) or "/../" in member.name:
                raise ValueError(f"refusing archive member path {member.name!r}")
            if member.name in files:
                raise ValueError(f"duplicate project member: {member.name}")
            if member.isfile():
                files[member.name] = archive.extractfile(member).read()
            elif not member.isdir():
                raise ValueError(f"unexpected member type in project TAR: {member.name}")
    return files


def _v7_header(name: str, size: int | None, mode: int, typeflag: bytes) -> bytes:
    encoded = name.encode("ascii")
    if not encoded or len(encoded) > 100:
        raise ValueError(f"member name must be 1..100 ASCII bytes: {name!r}")
    header = bytearray(TAR_BLOCK)
    header[0:len(encoded)] = encoded
    header[100:108] = b"%07o\0" % mode
    if size is not None:
        digits = b"%o" % size
        header[124:124 + len(digits)] = digits
    header[148:156] = b" " * 8
    header[156:157] = typeflag
    checksum = b"%o\0" % sum(header)
    header[148:156] = checksum.ljust(8, b" ")
    return bytes(header)


def pack_project(files: dict[str, bytes]) -> bytes:
    """Device-flavour TAR of the given regular files plus a directory entry per parent."""
    names = set(files)
    directories: set[str] = set()
    for name in names:
        if not name or name.startswith("/") or ".." in name.split("/") or name.endswith("/"):
            raise ValueError(f"invalid member name {name!r}")
        parts = name.split("/")
        for depth in range(1, len(parts)):
            directories.add("/".join(parts[:depth]))
    if directories & names:
        raise ValueError(f"name used as both file and directory: {sorted(directories & names)}")
    out = bytearray()
    for name in sorted(names | directories):
        if name in directories:
            out += _v7_header(name, None, DIR_MODE, b"5")
            continue
        data = files[name]
        out += _v7_header(name, len(data), FILE_MODE, b"0")
        out += data
        out += bytes(-len(data) % TAR_BLOCK)
    out += bytes(2 * TAR_BLOCK)
    return bytes(out)


# --- .ppak container -----------------------------------------------------------------

def project_entry(project: int) -> str:
    if type(project) is not int or not 1 <= project <= 9:
        raise ValueError("project must be an integer from 1 to 9")
    return f"/projects/P{project:02}.tar"


def generated_at(now: float | None = None) -> str:
    """ISO 8601 UTC with milliseconds, the form Sample Tool emits and accepts."""
    t = time.time() if now is None else now
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int(t * 1000) % 1000:03d}Z"


def project_meta(source: dict | None = None, now: float | None = None) -> dict:
    """meta.json for a single-project export: source fields kept, pak_type forced, timestamp fresh."""
    source = source or {}
    meta = {
        "info": PAK_INFO,
        "pak_version": 1,
        "pak_type": "project",
        "pak_release": source.get("pak_release", PAK_RELEASE),
        "device_name": source.get("device_name", "EP-133"),
        "device_sku": source.get("device_sku", "TE032AS001"),
        "device_version": source.get("device_version"),
        "generated_at": generated_at(now),
        "author": "computer",
        "base_sku": source.get("base_sku", source.get("device_sku", "TE032AS001")),
    }
    if meta["device_version"] is None:
        raise ValueError("device_version is required in meta (copy it from a backup's meta.json)")
    return {key: meta[key] for key in META_KEYS}


def build_ppak(project: int, tar: bytes, meta: dict, sounds: dict[str, bytes] | None = None,
               now: float | None = None) -> bytes:
    """ZIP bytes: /projects/PNN.tar, then /sounds/* in name order, then /meta.json; deflate, leading slashes."""
    if set(meta) != set(META_KEYS) or meta["pak_type"] != "project":
        raise ValueError("meta must come from project_meta()")
    stamp = time.localtime(time.time() if now is None else now)[:6]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as z:
        def add(name: str, data: bytes) -> None:
            info = zipfile.ZipInfo(name, date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)

        add(project_entry(project), tar)
        for name in sorted(sounds or {}):
            if not name.startswith("/sounds/") or "/" in name[len("/sounds/"):]:
                raise ValueError(f"sound entries must be /sounds/<slot> <name>.wav: {name!r}")
            add(name, sounds[name])
        add("/meta.json", (json.dumps(meta, indent=2) + "\n").encode())
    return buffer.getvalue()


def read_pak(data: bytes) -> tuple[dict, dict[int, bytes], dict[str, bytes]]:
    """(meta, {project number: TAR bytes}, {'/sounds/...': bytes}) from a .pak or .ppak."""
    if len(data) > 128 * 1024 * 1024:
        raise ValueError("archive exceeds 128 MiB limit")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        if sum(m.file_size for m in z.infolist()) > 128 * 1024 * 1024:
            raise ValueError("expanded archive exceeds 128 MiB limit")
        names = z.namelist()
        clean_names = [n.lstrip("/") for n in names]
        if len(clean_names) != len(set(clean_names)):
            raise ValueError("duplicate archive members")
        if any(".." in n.split("/") or "\\" in n for n in names):
            raise ValueError("unsafe archive member name")
        meta_name = next((n for n in names if n.lstrip("/") == "meta.json"), None)
        if meta_name is None:
            raise ValueError("pak has no meta.json")
        meta = json.loads(z.read(meta_name))
        if not isinstance(meta, dict):
            raise ValueError("meta.json must be an object")
        projects: dict[int, bytes] = {}
        sounds: dict[str, bytes] = {}
        for name in names:
            clean = name.lstrip("/")
            if clean.startswith("projects/P") and clean.endswith(".tar") and clean[10:12].isdigit():
                projects[int(clean[10:12])] = z.read(name)
            elif clean.startswith("sounds/") and clean.count("/") == 1:
                sounds["/" + clean] = z.read(name)
    return meta, projects, sounds


def referenced_sounds(tar: bytes, sounds: dict[str, bytes]) -> dict[str, bytes]:
    """The /sounds entries whose slot number a pad record in this project stores."""
    slots = {pad["stored_slot"] for pad in stored_pads(tar) if pad["stored_slot"]}
    out = {}
    for name, data in sounds.items():
        head = name[len("/sounds/"):].split(" ", 1)[0]
        if head.isdigit() and int(head) in slots:
            out[name] = data
    return out
