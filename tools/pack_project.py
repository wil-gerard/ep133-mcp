#!/usr/bin/env python3
"""Repack one project from a .pak/.ppak into a device-flavour .ppak (import ladder rung 2).

    pack_project.py <source.pak|.ppak> --project N --out <file.ppak> [--no-sounds]
    pack_project.py verify <source.pak|.ppak> [--project N]

`verify` unpacks every project (or one) and repacks it, reporting whether the
TAR came back byte-identical; nothing is written. The default command writes
the .ppak with the project's TAR repacked by protocol.projects.pack_project,
the /sounds entries its pad records reference copied from the source, and a
fresh meta.json (pak_type "project"). It refuses to write unless the repacked
TAR is byte-identical to the source, so what reaches the device is exactly the
device's own bytes in our container. No MIDI port is opened. Backups stay
where they are; never commit the output.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ep133_mcp.protocol import projects  # noqa: E402


def verify(source: Path, project: int | None) -> int:
    meta, paks, _ = projects.read_pak(source.read_bytes())
    failures = 0
    for number, tar in sorted(paks.items()):
        if project is not None and number != project:
            continue
        again = projects.pack_project(projects.unpack_project(tar))
        status = "identical" if again == tar else "DIFFERS"
        failures += again != tar
        print(f"P{number:02}: {len(tar)} bytes, {status}")
    print(f"device_version {meta.get('device_version')}, pak_type {meta.get('pak_type')}, failures: {failures}")
    return 1 if failures else 0


def pack(source: Path, project: int, out: Path, include_sounds: bool) -> int:
    meta, paks, sounds = projects.read_pak(source.read_bytes())
    if project not in paks:
        print(f"source has no project P{project:02}", file=sys.stderr)
        return 2
    tar = paks[project]
    repacked = projects.pack_project(projects.unpack_project(tar))
    if repacked != tar:
        print("repacked TAR differs from the source; refusing to write", file=sys.stderr)
        return 1
    chosen = projects.referenced_sounds(tar, sounds) if include_sounds else {}
    data = projects.build_ppak(project, repacked, projects.project_meta(meta), chosen)
    out.write_bytes(data)
    print(f"wrote {out} ({len(data)} bytes): /projects/P{project:02}.tar {len(tar)} bytes identical to source, "
          f"{len(chosen)} sound entries, sha256 {hashlib.sha256(data).hexdigest()[:16]}…")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("args", nargs="+", help="[verify] <source.pak|.ppak>")
    ap.add_argument("--project", type=int)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--no-sounds", action="store_true")
    ns = ap.parse_args(argv)
    if ns.args[0] == "verify":
        if len(ns.args) != 2:
            ap.error("verify takes exactly one source path")
        return verify(Path(ns.args[1]), ns.project)
    if len(ns.args) != 1 or ns.project is None or ns.out is None:
        ap.error("packing needs <source>, --project and --out")
    if ns.out.suffix != ".ppak":
        ap.error("--out must end in .ppak")
    return pack(Path(ns.args[0]), ns.project, ns.out, not ns.no_sounds)


if __name__ == "__main__":
    sys.exit(main())
