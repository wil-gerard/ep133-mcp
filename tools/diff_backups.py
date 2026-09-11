"""Diff pad records between two .pak backups.

Reads stored pad-record bytes, which the device does not expose over SysEx —
`FILE_METADATA_GET` returns the resolved `sym`, not the stored slot field. A
pad whose stored field points at a deleted slot reads 0 live, so live reads
cannot tell "reverted to empty" from "still holds a stale id".

That distinction is exactly what the restore-drift question turns on, so
comparing two backups is the only way to see it.

    python tools/diff_backups.py OLD.pak NEW.pak [--project 5]
"""

import argparse
import io
import re
import struct
import sys
import tarfile
import zipfile


def pads(pak):
    z = zipfile.ZipFile(pak)
    sounds = {int(m.group(1)) for n in z.namelist() if (m := re.match(r"/sounds/(\d+) ", n))}
    out = {}
    for name in sorted(n for n in z.namelist() if re.match(r"/projects/P\d+\.tar$", n)):
        pi = int(re.search(r"P(\d+)\.tar$", name).group(1))
        tf = tarfile.open(fileobj=io.BytesIO(z.read(name)))
        members = set(tf.getnames())
        for gi, g in enumerate("abcd"):
            for p in range(1, 13):
                m = f"pads/{g}/p{p:02d}"
                if m in members:
                    rec = tf.extractfile(m).read()
                    out[(pi, g.upper(), p)] = {
                        "slot": struct.unpack_from("<H", rec, 1)[0],
                        "len": struct.unpack_from("<I", rec, 8)[0],
                        "raw": rec.hex(),
                    }
    return sounds, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("--project", type=int, default=None)
    args = ap.parse_args()

    old_sounds, old = pads(args.old)
    new_sounds, new = pads(args.new)

    print(f"library slots: {len(old_sounds)} -> {len(new_sounds)}")
    added, removed = sorted(new_sounds - old_sounds), sorted(old_sounds - new_sounds)
    if added:
        print(f"  added:   {added}")
    if removed:
        print(f"  removed: {removed}")

    keys = sorted(set(old) | set(new))
    diffs = 0
    for k in keys:
        if args.project and k[0] != args.project:
            continue
        a, b = old.get(k), new.get(k)
        if a == b:
            continue
        diffs += 1
        pi, g, p = k
        node = 2000 + pi * 1000 + 200 + "ABCD".index(g) * 100 + p
        print(f"\nP{pi:02d}/{g}/p{p:02d}  (node {node})")
        for label, rec in (("old", a), ("new", b)):
            if rec is None:
                print(f"  {label}: <absent>")
            else:
                print(f"  {label}: slot={rec['slot']:<5} len={rec['len']:<8} {rec['raw']}")

    print(f"\npad records differing: {diffs}")


if __name__ == "__main__":
    main()
