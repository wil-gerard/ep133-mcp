"""Device-flavour project packing on a synthesized minimal project.

The byte-exactness claim against the real backup is checked locally with
`tools/pack_project.py verify <backup.pak>`; backups never enter the repo. These
tests pin the header flavour with hand-built expectations so a regression in
any field is named, and prove the container round trip end to end.
"""

import io
import json
import struct
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from ep133_mcp.protocol import projects as P

sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))
import pack_project as pack_project_tool  # noqa: E402


def minimal_project(pad7_slot: int = 16) -> dict[str, bytes]:
    files = {"fx_settings": bytes(160), "scenes": bytes(712), "settings": struct.pack("<4xf", 120.0) + bytes(214)}
    for group in "abcd":
        for pad in range(1, 13):
            record = bytearray(27)
            if pad == 7 and group == "a":
                struct.pack_into("<H", record, 1, pad7_slot)
                struct.pack_into("<I", record, 8, 37500)
            files[f"pads/{group}/p{pad:02}"] = bytes(record)
    files["patterns/a01"] = bytes([0, 1, 1, 0]) + struct.pack("<HBBBHB", 0, 7, 60, 100, 24, 0)
    files["patterns/b03"] = bytes([0, 2, 0, 0])
    return files


def headers(tar: bytes) -> list[bytes]:
    out, offset = [], 0
    while tar[offset:offset + 512] != bytes(512):
        block = tar[offset:offset + 512]
        out.append(block)
        size = int(block[124:136].rstrip(b"\0") or b"0", 8)
        offset += 512 + (size + 511) // 512 * 512
    return out


def expected_header(name: bytes, size: bytes, mode: bytes, typeflag: bytes) -> bytes:
    header = bytearray(512)
    header[:len(name)] = name
    header[100:108] = mode
    header[124:124 + len(size)] = size
    header[148:156] = b" " * 8
    header[156:157] = typeflag
    header[148:156] = (b"%o\0" % sum(header)).ljust(8, b" ")
    return bytes(header)


def test_pack_is_device_flavour():
    tar = P.pack_project(minimal_project())
    assert len(tar) % 512 == 0 and tar.endswith(bytes(1024)) and not tar.endswith(bytes(1536))
    blocks = headers(tar)
    names = [b[:100].rstrip(b"\0").decode() for b in blocks]
    assert names[:4] == ["fx_settings", "pads", "pads/a", "pads/a/p01"]
    assert names[-6:] == ["pads/d/p12", "patterns", "patterns/a01", "patterns/b03", "scenes", "settings"]
    assert names == sorted(names) and len(names) == 3 + 6 + 48 + 2
    assert blocks[1] == expected_header(b"pads", b"", b"0000755\0", b"5")
    assert blocks[3] == expected_header(b"pads/a/p01", b"33", b"0000644\0", b"0")
    assert blocks[-1] == expected_header(b"settings", b"336", b"0000644\0", b"0")
    for block in blocks:
        assert block[108:124] == bytes(16) and block[136:148] == bytes(12)     # uid, gid, mtime all NUL
        assert block[257:] == bytes(255)                                        # no ustar magic or trailer
        assert block[148:156].rstrip(b" ").endswith(b"\0")


def test_unpack_pack_round_trip():
    files = minimal_project()
    tar = P.pack_project(files)
    assert P.unpack_project(tar) == files
    assert P.pack_project(P.unpack_project(tar)) == tar
    with tarfile.open(fileobj=io.BytesIO(tar), mode="r:") as archive:     # the stdlib reads it too
        assert {m.name for m in archive.getmembers() if m.isdir()} == {"pads", "pads/a", "pads/b", "pads/c", "pads/d", "patterns"}
        assert archive.getmember("pads/a/p07").mode == 0o644
    assert P.stored_pads(tar)[6] == {"group": "A", "pad": 7, "label": "1", "stored_slot": 16, "stored_length": 37500}


@pytest.mark.parametrize("bad", [{"/abs": b""}, {"a/../b": b""}, {"dir/": b""}, {"pads": b"", "pads/a": b""}, {"x" * 101: b""}])
def test_pack_rejects_bad_names(bad):
    with pytest.raises(ValueError):
        P.pack_project(bad)


def test_unpack_rejects_truncated():
    tar = P.pack_project(minimal_project())
    with pytest.raises(ValueError):
        P.unpack_project(tar[:-512])


def test_ppak_container_round_trip():
    tar = P.pack_project(minimal_project())
    meta = P.project_meta({"device_version": "2.5.1", "pak_type": "user"}, now=1_800_000_000.5)
    assert meta["pak_type"] == "project" and meta["generated_at"] == "2027-01-15T08:00:00.500Z"
    assert list(meta) == list(P.META_KEYS)
    sounds = {"/sounds/016 tone.wav": b"RIFF", "/sounds/017 other.wav": b"RIFX"}
    data = P.build_ppak(5, tar, meta, sounds)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert z.namelist() == ["/projects/P05.tar", "/sounds/016 tone.wav", "/sounds/017 other.wav", "/meta.json"]
        assert all(i.compress_type == zipfile.ZIP_DEFLATED for i in z.infolist())
        assert json.loads(z.read("/meta.json")) == meta
    back_meta, back_projects, back_sounds = P.read_pak(data)
    assert back_meta == meta and back_projects == {5: tar} and back_sounds == sounds
    assert P.referenced_sounds(tar, sounds) == {"/sounds/016 tone.wav": b"RIFF"}
    with pytest.raises(ValueError):
        P.build_ppak(5, tar, {**meta, "pak_type": "user"})
    with pytest.raises(ValueError):
        P.build_ppak(5, tar, meta, {"/other/x.wav": b""})
    with pytest.raises(ValueError):
        P.project_meta({})


def stdlib_tar(files: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w") as t:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return out.getvalue()


def test_pack_project_tool(tmp_path, capsys):
    files = minimal_project()
    meta = {"pak_type": "user", "device_version": "2.5.1", "device_sku": "TE032AS001"}
    source = tmp_path / "backup.pak"
    with zipfile.ZipFile(source, "w") as z:
        z.writestr("/meta.json", json.dumps(meta))
        z.writestr("/projects/P05.tar", P.pack_project(files))
        z.writestr("/projects/P06.tar", stdlib_tar(files))       # ustar headers: not the device flavour
        z.writestr("/sounds/016 tone.wav", b"RIFF")
        z.writestr("/sounds/099 unused.wav", b"RIFF")
    assert pack_project_tool.main(["verify", str(source)]) == 1
    out = capsys.readouterr().out
    assert "P05: " in out and "identical" in out and "P06: " in out and "DIFFERS" in out and "failures: 1" in out
    assert pack_project_tool.main(["verify", str(source), "--project", "5"]) == 0

    target = tmp_path / "P05.ppak"
    assert pack_project_tool.main([str(source), "--project", "5", "--out", str(target)]) == 0
    back_meta, back_projects, back_sounds = P.read_pak(target.read_bytes())
    assert back_projects == {5: P.pack_project(files)} and list(back_sounds) == ["/sounds/016 tone.wav"]
    assert back_meta["pak_type"] == "project" and back_meta["device_version"] == "2.5.1"
    assert pack_project_tool.main([str(source), "--project", "6", "--out", str(tmp_path / "P06.ppak")]) == 1
    assert not (tmp_path / "P06.ppak").exists()
    assert pack_project_tool.main([str(source), "--project", "7", "--out", str(tmp_path / "P07.ppak")]) == 2
    with pytest.raises(SystemExit):
        pack_project_tool.main([str(source), "--project", "5", "--out", str(tmp_path / "x.pak")])
