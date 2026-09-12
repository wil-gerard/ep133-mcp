#!/usr/bin/env python3
"""Offline decoder for EP-133 project sequencer files.

Reads the project TARs inside a Sample Tool backup (`.pak` / `.ppak` ZIP), a
bare `PNN.tar`, or an already-extracted project directory, and decodes:

    patterns/{a-d}NN   4-byte header + N x 8-byte events
    scenes             head chunk + 99 x 6-byte scene chunks + trailer
    settings           BPM float32 LE at bytes 4..7 + 48 float32 params
    fx_settings        effect selector + 34 float32 params

Field meanings and their verification status are documented in
docs/research/pattern-encoding.md. This tool never opens a MIDI port and
never prints sample names or the device serial: it only reads `projects/`.

    pattern_decode.py dump  <backup.pak|PNN.tar|project_dir> [--project P05]
    pattern_decode.py check <backup.pak|PNN.tar|project_dir>
    pattern_decode.py json  <backup.pak|PNN.tar|project_dir>
"""

from __future__ import annotations

import argparse
import io
import json
import os
import struct
import sys
import tarfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ep133_mcp.protocol import decode as D  # noqa: E402

TICKS_PER_BAR = 384
EVENT_SIZE = 8
PATTERN_HEADER = 4
SCENES_HEAD = 7
SCENE_CHUNK = 6
SCENE_SLOTS = 99
EVENT_TYPE_NOTE = 0
EVENT_TYPE_PARAM = 1


# ----- Loading ---------------------------------------------------------------


def _safe_name(name: str) -> str:
    name = name.lstrip("/")
    if name.startswith("../") or "/../" in name or name == "..":
        raise ValueError(f"refusing path traversal in archive member {name!r}")
    return name


def _read_tar(data: bytes) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tf:
        for m in tf.getmembers():
            if m.isfile():
                f = tf.extractfile(m)
                if f is not None:
                    files[_safe_name(m.name)] = f.read()
    return files


def load_projects(path: str) -> dict[str, dict[str, bytes]]:
    """Return {project_name: {member_name: bytes}} for every project found."""
    if os.path.isdir(path):
        files = {}
        for root, _dirs, names in os.walk(path):
            for n in names:
                full = os.path.join(root, n)
                rel = os.path.relpath(full, path)
                files[rel] = open(full, "rb").read()
        return {os.path.basename(os.path.abspath(path)): files}
    if zipfile.is_zipfile(path):
        out = {}
        with zipfile.ZipFile(path) as zf:
            for n in zf.namelist():
                clean = _safe_name(n)
                if clean.startswith("projects/") and clean.endswith(".tar"):
                    out[os.path.basename(clean)[:-4]] = _read_tar(zf.read(n))
        return out
    data = open(path, "rb").read()
    return {os.path.basename(path).rsplit(".", 1)[0]: _read_tar(data)}


# ----- Decoding --------------------------------------------------------------
# The decoders live in the package (ep133_mcp.protocol.decode) so read_project and this
# CLI cannot drift apart; these wrappers only reshape their output for the dump/check commands.


def decode_pattern(b: bytes) -> dict:
    decoded = D.decode_pattern_file(b)
    events = []
    for off in range(PATTERN_HEADER, len(b), EVENT_SIZE):
        e = b[off:off + EVENT_SIZE]
        pos, = struct.unpack_from("<H", e, 0)
        typ = e[2] & 7
        ev = {"pos": pos, "type": typ, "byte7": e[7], "raw": e.hex()}
        if typ == EVENT_TYPE_NOTE:
            ev.update(pad=(e[2] >> 3) + 1, note=e[3], vel=e[4], dur=struct.unpack_from("<H", e, 5)[0])
        else:
            ev.update(padbits=e[2] >> 3, param=e[3], byte4=e[4], value=struct.unpack_from("<H", e, 5)[0])
        events.append(ev)
    assert len(events) == len(decoded["events"]) + len(decoded["automation"]) + len(decoded["other"])
    return {"header": b[:PATTERN_HEADER].hex(), "byte0": b[0], "bars": decoded["bars"], "count": b[2],
            "byte3": b[3], "events": events}


def decode_scenes(b: bytes) -> dict:
    d = D.decode_scenes(b)
    trailer = bytes.fromhex(d["trailer"])
    out = {
        "size": d["size"],
        "head": {"byte0": b[0], "live": [d["live"][g] for g in "ABCD"], "num": d["live"]["num"], "den": d["live"]["den"]},
        "populated": [{"scene": c["scene"], "a": c["A"], "b": c["B"], "c": c["C"], "d": c["D"],
                       "num": c["num"], "den": c["den"]} for c in d["scenes"]],
        "trailer": d["trailer"],
        "trailer_u32be": [struct.unpack_from(">I", trailer, o)[0] for o in range(0, len(trailer) - 3, 4)][:3],
    }
    if "song" in d:
        out["song_len"] = len(d["song"])
        out["song_positions"] = d["song"]
    return out


def decode_settings(b: bytes) -> dict:
    d = D.decode_settings(b)
    return {"size": d["size"], "bytes0_3": b[0:4].hex(), "bpm": d["bpm"], "bytes8_23": b[8:24].hex(),
            "params": d["params"], "group_bytes": d["group_bytes"], "tail": d["tail"]}


def decode_fx_settings(b: bytes) -> dict:
    d = D.decode_fx_settings(b)
    return {"size": d["size"], "bytes0_3": b[0:4].hex(), "fx": d["selector"], "bytes5_7": b[5:8].hex(),
            "params": d["params"], "tail": d["tail"]}


def decode_project(files: dict[str, bytes]) -> dict:
    out = {"patterns": {}, "scenes": None, "settings": None, "fx_settings": None}
    for name in sorted(files):
        if name.startswith("patterns/"):
            out["patterns"][name.split("/", 1)[1]] = decode_pattern(files[name])
    if "scenes" in files:
        out["scenes"] = decode_scenes(files["scenes"])
    if "settings" in files:
        out["settings"] = decode_settings(files["settings"])
    if "fx_settings" in files:
        out["fx_settings"] = decode_fx_settings(files["fx_settings"])
    return out


# ----- Commands --------------------------------------------------------------


def cmd_dump(projects: dict[str, dict[str, bytes]]) -> None:
    for pname, files in sorted(projects.items()):
        d = decode_project(files)
        print(f"== {pname}")
        if d["settings"]:
            s = d["settings"]
            print(f"  settings {s['size']}B  bpm={s['bpm']:g}  group_bytes={s['group_bytes']}  tail={s['tail'] or '-'}")
        if d["fx_settings"]:
            f = d["fx_settings"]
            print(f"  fx_settings {f['size']}B  fx={f['fx']}  non-default params: "
                  + ", ".join(f"{i}={v:.4g}" for i, v in enumerate(f["params"]) if v != 0.5))
        if d["scenes"]:
            sc = d["scenes"]
            print(f"  scenes {sc['size']}B  live={sc['head']['live']} {sc['head']['num']}/{sc['head']['den']}"
                  f"  trailer_u32be={sc['trailer_u32be']}"
                  + (f"  song={sc['song_positions']}" if "song_positions" in sc else ""))
            for c in sc["populated"]:
                print(f"    scene {c['scene']:2d}: a{c['a']:02d} b{c['b']:02d} c{c['c']:02d} d{c['d']:02d}")
        for name, p in d["patterns"].items():
            print(f"  patterns/{name}  bars={p['bars']} events={p['count']}")
            for e in p["events"]:
                bar = e["pos"] // TICKS_PER_BAR + 1
                tick = e["pos"] % TICKS_PER_BAR
                if e["type"] == EVENT_TYPE_NOTE:
                    print(f"    {e['pos']:5d} (bar {bar} +{tick:3d})  pad {e['pad']:2d}  note {e['note']:3d}"
                          f"  vel {e['vel']:3d}  dur {e['dur']:5d}  b7 {e['byte7']:3d}")
                else:
                    print(f"    {e['pos']:5d} (bar {bar} +{tick:3d})  PARAM type={e['type']} id={e['param']}"
                          f"  value {e['value']:5d}  b4 {e['byte4']}  b7 {e['byte7']:3d}")


def cmd_json(projects: dict[str, dict[str, bytes]]) -> None:
    json.dump({p: decode_project(f) for p, f in sorted(projects.items())}, sys.stdout, indent=1)
    print()


def cmd_check(projects: dict[str, dict[str, bytes]]) -> int:
    """Re-run the invariants behind docs/research/pattern-encoding.md."""
    failures = 0
    totals = {"patterns": 0, "note_events": 0, "param_events": 0, "over_length": 0}

    def fail(msg: str) -> None:
        nonlocal failures
        failures += 1
        print("FAIL", msg)

    for pname, files in sorted(projects.items()):
        have = {n.split("/", 1)[1] for n in files if n.startswith("patterns/")}
        for name in sorted(files):
            if not name.startswith("patterns/"):
                continue
            b = files[name]
            totals["patterns"] += 1
            if (len(b) - PATTERN_HEADER) % EVENT_SIZE:
                fail(f"{pname}/{name}: size {len(b)} is not 4+8n"); continue
            p = decode_pattern(b)
            if p["byte0"] or p["byte3"]:
                fail(f"{pname}/{name}: header bytes 0/3 nonzero: {p['header']}")
            if p["count"] != len(p["events"]):
                fail(f"{pname}/{name}: header count {p['count']} != {len(p['events'])} events")
            prev = -1
            for e in p["events"]:
                if e["pos"] < prev:
                    fail(f"{pname}/{name}: events not sorted at pos {e['pos']}")
                prev = e["pos"]
                if e["pos"] >= p["bars"] * TICKS_PER_BAR:
                    totals["over_length"] += 1
                if e["type"] == EVENT_TYPE_NOTE:
                    totals["note_events"] += 1
                    if not 1 <= e["pad"] <= 12:
                        fail(f"{pname}/{name}: pad {e['pad']} out of range")
                    if e["note"] > 127:
                        fail(f"{pname}/{name}: note {e['note']} out of range")
                elif e["type"] == EVENT_TYPE_PARAM:
                    totals["param_events"] += 1
                    if e["padbits"] or e["byte4"]:
                        fail(f"{pname}/{name}: param event with padbits/byte4 set: {e['raw']}")
                else:
                    fail(f"{pname}/{name}: unknown event type {e['type']}: {e['raw']}")
        if "scenes" in files:
            sc = decode_scenes(files["scenes"])
            if sc["size"] not in (612, 712):
                fail(f"{pname}/scenes: unexpected size {sc['size']}")
            if (sc["head"]["num"], sc["head"]["den"]) != (4, 4):
                print(f"NOTE {pname}/scenes: head time signature {sc['head']['num']}/{sc['head']['den']}")
            for c in sc["populated"]:
                if (c["num"], c["den"]) != (4, 4):
                    print(f"NOTE {pname}/scenes: scene {c['scene']} time signature {c['num']}/{c['den']}")
                for g in "abcd":
                    if c[g] == 0:
                        print(f"NOTE {pname}/scenes: scene {c['scene']} has a 0 entry for group {g}")
                    elif f"{g}{c[g]:02d}" not in have:
                        print(f"NOTE {pname}/scenes: scene {c['scene']} references missing patterns/{g}{c[g]:02d}")
        else:
            fail(f"{pname}: no scenes file")
        for fname, sizes in (("settings", (220, 222, 224)), ("fx_settings", (144, 160))):
            if fname not in files:
                fail(f"{pname}: no {fname} file")
            elif len(files[fname]) not in sizes:
                fail(f"{pname}/{fname}: unexpected size {len(files[fname])}")
        if "settings" in files:
            bpm = decode_settings(files["settings"])["bpm"]
            if not 20.0 <= bpm <= 400.0:
                fail(f"{pname}/settings: implausible bpm {bpm}")
    print("totals:", totals)
    print("failures:", failures)
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("dump", "check", "json"))
    ap.add_argument("path", help=".pak/.ppak, PNN.tar, or extracted project directory")
    ap.add_argument("--project", help="only this project (e.g. P05)")
    args = ap.parse_args(argv)
    projects = load_projects(args.path)
    if args.project:
        projects = {k: v for k, v in projects.items() if k == args.project}
        if not projects:
            print(f"no project {args.project} found", file=sys.stderr)
            return 2
    if args.command == "dump":
        cmd_dump(projects)
    elif args.command == "json":
        cmd_json(projects)
    else:
        return cmd_check(projects)
    return 0


if __name__ == "__main__":
    sys.exit(main())
