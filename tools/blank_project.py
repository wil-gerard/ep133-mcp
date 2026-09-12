#!/usr/bin/env python3
"""Write a .ppak that empties one project of a .pak backup.

    blank_project.py BACKUP.pak --project N --out OUT.ppak [--bpm 120]

`generate_ppak` patches a project: it can replace a pattern but not remove one, and it never
clears a pad. Emptying a project is the other operation, and it is a whole-file rewrite, so it
lives here.

The output keeps the project TAR's own member list and flavour - every `pads/<group>/pNN`, every
`patterns/*`, `scenes`, `settings`, `fx_settings`, with each member's original size - and only
changes contents: pad records keep every byte except the slot (0) and length (0) the device reads
as "empty", pattern files become their 4-byte header with zero events, every scene chunk's four
group indices go to 1 with the selected scene set to 1 (the 04 04 pair is kept), and `settings`
keeps everything but the BPM. Scenes are the fresh-project state, not zeros: a project the device
initialised itself (P6, 2026-09-09 backup) holds 01 01 01 01 in all 99 chunks, and no project the
device ever wrote has chunk 1 zeroed - eight projects blanked with zeroed chunks on 2026-09-12 all
errored on the device when selected. Sounds referenced by the
emptied project are not included and nothing in the sample library is touched: emptying a project
frees no sample space.

Reads only the backup; never opens MIDI. Import the result into the project it names, which must
not be the active project, exactly like any other .ppak.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ep133_mcp.protocol import patterns as enc  # noqa: E402
from ep133_mcp.protocol.projects import (build_ppak, pack_project, project_meta,  # noqa: E402
                                         read_pak, unpack_project)

def scene_populated(scenes: bytes, scene: int) -> bool:
    """A scene chunk is 4 group pattern indices then a 04 04 pair; unpopulated means the four are 0."""
    offset = enc.scene_chunk_offset(scene)
    return offset + 4 <= len(scenes) and scenes[offset:offset + 4] != bytes(4)


def blank_pad(record: bytes) -> bytes:
    """Slot 0 and length 0; every other byte of the device's record is kept."""
    out = bytearray(record)
    struct.pack_into("<H", out, 1, 0)
    struct.pack_into("<I", out, 8, 0)
    return bytes(out)


FRESH_CHUNK = bytes([1, 1, 1, 1])   # pattern 1 of every group: what the device writes in a new project
SELECTED_SCENE_OFFSET = enc.SCENES_HEAD + enc.SCENE_CHUNK * enc.SCENE_SLOTS   # big-endian u32, 1-based


def blank_scenes(scenes: bytes) -> bytes:
    """Every scene chunk to pattern 1 of each group and the selected scene to 1, leaving the head,
    the rest of the trailer and each chunk's 04 04 pair."""
    out = bytearray(scenes)
    for scene in range(1, enc.SCENE_SLOTS + 1):
        offset = enc.scene_chunk_offset(scene)
        out[offset:offset + 4] = FRESH_CHUNK
    struct.pack_into(">I", out, SELECTED_SCENE_OFFSET, 1)
    return bytes(out)


def blank_project(tar: bytes, bpm: float | None = None) -> tuple[bytes, dict]:
    files = unpack_project(tar)
    report = {"pads_cleared": 0, "patterns_emptied": 0, "scenes_cleared": 0}
    for name, data in list(files.items()):
        if name.startswith("pads/"):
            if struct.unpack_from("<I", data, 8)[0] or struct.unpack_from("<H", data, 1)[0]:
                report["pads_cleared"] += 1
            files[name] = blank_pad(data)
        elif name.startswith("patterns/"):
            if len(data) > 4:
                report["patterns_emptied"] += 1
            files[name] = bytes([data[0], data[1] or 1, 0, data[3]])
    if "scenes" in files:
        before = files["scenes"]
        files["scenes"] = blank_scenes(before)
        report["scenes_cleared"] = sum(1 for scene in range(1, enc.SCENE_SLOTS + 1)
                                       if before[enc.scene_chunk_offset(scene):][:4] != FRESH_CHUNK)
    if bpm is not None and "settings" in files:
        files["settings"] = enc.patch_bpm(files["settings"], bpm)
    return pack_project(files), report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("backup", type=Path)
    parser.add_argument("--project", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--bpm", type=float, default=None)
    args = parser.parse_args(argv)
    if not 1 <= args.project <= 9:
        parser.error("project must be 1..9")
    if args.out.suffix != ".ppak":
        parser.error("--out must end in .ppak")
    if args.out.exists():
        print(f"refusing to overwrite {args.out}")
        return 1
    meta, projects, _ = read_pak(args.backup.read_bytes())
    if args.project not in projects:
        print(f"backup holds no project P{args.project:02d}")
        return 2
    tar, report = blank_project(projects[args.project], args.bpm)
    args.out.write_bytes(build_ppak(args.project, tar, project_meta(meta)))
    print(f"wrote {args.out} ({args.out.stat().st_size} bytes): P{args.project:02d} "
          f"{len(tar)} bytes, {report['pads_cleared']} pads cleared, "
          f"{report['patterns_emptied']} patterns emptied, {report['scenes_cleared']} scenes cleared")
    print("Import into that project, which must not be the active project. No sample is deleted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
