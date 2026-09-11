#!/usr/bin/env python3
"""Byte-level diff of project TARs between two .pak/.ppak files.

    diff_projects.py OLD.pak NEW.pak [--project N]

For each project present in either file: members added, removed, and changed,
with the differing byte ranges (offset, length, before/after hex) for changed
members of equal size and the sizes otherwise. Prints "no differences" when
every project matches byte for byte. Reads only projects/; never prints sample
names or the serial. Exit status 0 when identical, 1 when different.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ep133_mcp.protocol.generate import _ranges  # noqa: E402
from ep133_mcp.protocol.projects import read_pak, unpack_project  # noqa: E402


def diff(old: Path, new: Path, project: int | None) -> int:
    _, old_projects, _ = read_pak(old.read_bytes())
    _, new_projects, _ = read_pak(new.read_bytes())
    numbers = sorted(set(old_projects) | set(new_projects))
    if project is not None:
        numbers = [n for n in numbers if n == project]
    differences = 0
    for number in numbers:
        a, b = old_projects.get(number), new_projects.get(number)
        if a == b:
            continue
        if a is None or b is None:
            print(f"P{number:02}: {'added' if a is None else 'removed'}")
            differences += 1
            continue
        old_files, new_files = unpack_project(a), unpack_project(b)
        print(f"P{number:02}: {len(a)} -> {len(b)} bytes")
        for name in sorted(set(old_files) | set(new_files)):
            x, y = old_files.get(name), new_files.get(name)
            if x == y:
                continue
            differences += 1
            if x is None:
                print(f"  + {name} ({len(y)} bytes)")
            elif y is None:
                print(f"  - {name} ({len(x)} bytes)")
            elif len(x) != len(y):
                print(f"  ~ {name}: {len(x)} -> {len(y)} bytes")
            else:
                for r in _ranges(x, y):
                    print(f"  ~ {name} @{r['offset']}+{r['length']}: {r['before']} -> {r['after']}")
    if not differences:
        print("no differences")
    return 1 if differences else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("old", type=Path)
    ap.add_argument("new", type=Path)
    ap.add_argument("--project", type=int)
    ns = ap.parse_args(argv)
    return diff(ns.old, ns.new, ns.project)


if __name__ == "__main__":
    sys.exit(main())
