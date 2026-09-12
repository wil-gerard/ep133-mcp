"""read_project's decoder: every project member to JSON, parameter events kept apart from notes."""

import json
import struct
import sys
import zipfile
from pathlib import Path

import pytest

from ep133_mcp.protocol import decode as D
from ep133_mcp.protocol import patterns as enc
from ep133_mcp.protocol import projects as P
from test_pack_project import minimal_project

sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))
import pattern_decode as cli  # noqa: E402


def note(tick, pad, duration=24, note=60, byte7=0):
    return struct.pack("<HBBBHB", tick, (pad - 1) << 3, note, 100, duration, byte7)


def automation(tick, param, value, byte7=6):
    return struct.pack("<HBBBHB", tick, 1, param, 0, value, byte7)


def test_pattern_file_separates_notes_automation_and_unknown_types():
    unknown = struct.pack("<HBBBHB", 30, 2, 0, 0, 0, 0)
    data = bytes([0, 2, 4, 0]) + note(0, 7, 96, 67, 8) + automation(6, 5, 12898) + note(384, 12) + unknown
    out = D.decode_pattern_file(data)
    assert out["bars"] == 2
    assert out["events"] == [{"pad": 7, "tick": 0, "duration": 96, "note": 67, "byte4": 100, "byte7": 8},
                             {"pad": 12, "tick": 384, "duration": 24, "note": 60, "byte4": 100, "byte7": 0}]
    assert out["automation"] == [{"tick": 6, "param": 5, "value": 12898, "byte4": 0, "padbits": 0, "byte7": 6}]
    assert out["other"] == [{"tick": 30, "type": 2, "raw": unknown.hex()}]
    with pytest.raises(ValueError):
        D.decode_pattern_file(data[:-3])


def test_scenes_both_forms():
    scenes = bytearray(712)
    scenes[1:7] = bytes([1, 1, 1, 1, 4, 4])
    scenes = enc.patch_scene(bytes(scenes), 3, {"A": 2, "B": 1, "C": 1, "D": 9})
    scenes = scenes[:601] + struct.pack(">II", 3, 0) + bytes(3) + bytes([2, 3, 1]) + scenes[601 + 14:]
    out = D.decode_scenes(scenes)
    assert out["size"] == 712 and out["live"] == {"A": 1, "B": 1, "C": 1, "D": 1, "num": 4, "den": 4}
    assert out["scenes"] == [{"scene": 3, "A": 2, "B": 1, "C": 1, "D": 9, "num": 4, "den": 4}]
    assert out["selected_scene"] == 3 and out["song"] == [3, 1]
    older = bytes(612)
    assert D.decode_scenes(older)["scenes"] == [] and "song" not in D.decode_scenes(older)
    with pytest.raises(ValueError):
        D.decode_scenes(bytes(700))


def test_settings_and_fx_settings():
    settings = struct.pack("<4xf", 123.08) + struct.pack("<16x48f", *([-1.0] * 47 + [0.5])) + bytes([5, 5, 5, 5, 0, 2])
    assert len(settings) == 222
    out = D.decode_settings(settings)
    assert out["bpm"] == pytest.approx(123.08) and out["params"][-1] == 0.5 and out["params"][0] == -1.0
    assert out["group_bytes"] == [5, 5, 5, 5] and out["tail"] == "0002"
    with pytest.raises(ValueError):
        D.decode_settings(settings[:100])
    fx = bytes(4) + bytes([6, 0, 0, 0]) + struct.pack("<34f", *([0.5] * 33 + [235 / 256])) + bytes(16)
    out = D.decode_fx_settings(fx)
    assert out["selector"] == 6 and out["params"][33] == pytest.approx(235 / 256) and out["tail"] == "00" * 16
    with pytest.raises(ValueError):
        D.decode_fx_settings(fx[:50])


def test_decode_project_minimal():
    tar = P.pack_project(minimal_project())
    out = D.decode_project(tar)
    assert out["bpm"] == 120.0
    assert len(out["pads"]) == 48
    pad7 = next(p for p in out["pads"] if p["group"] == "A" and p["pad"] == 7)
    assert pad7["stored_slot"] == 16 and pad7["stored_length"] == 37500 and pad7["label"] == "1"
    assert [(p["group"], p["index"], p["bars"], len(p["events"])) for p in out["patterns"]] == [("A", 1, 1, 1), ("B", 3, 2, 0)]
    assert out["patterns"][0]["events"][0] == {"pad": 7, "tick": 0, "duration": 24, "note": 60, "byte4": 100, "byte7": 0}
    assert out["scenes"]["scenes"] == [] and out["fx_settings"]["selector"] == 0
    assert out["members"]["scenes"] == 712
    assert json.dumps(out)  # the MCP tool returns it as-is


def test_decode_project_rejects_incomplete():
    files = minimal_project()
    del files["pads/c/p05"]
    with pytest.raises(ValueError):
        D.decode_project(P.pack_project(files))
    files = minimal_project()
    files["patterns/e01"] = bytes(4)
    with pytest.raises(ValueError):
        D.decode_project(P.pack_project(files))


def test_cli_and_package_agree():
    """tools/pattern_decode.py is a wrapper over the same decoders; its event view must match."""
    files = minimal_project()
    files["patterns/c02"] = bytes([0, 1, 3, 0]) + note(10, 3, 50, 72, 97) + automation(12, 1, 300) + note(300, 1)
    tar = P.pack_project(files)
    package = D.decode_project(tar)
    tool = cli.decode_project(P.unpack_project(tar))
    for pattern in package["patterns"]:
        view = tool["patterns"][f"{pattern['group'].lower()}{pattern['index']:02d}"]
        notes = [e for e in view["events"] if e["type"] == 0]
        params = [e for e in view["events"] if e["type"] == 1]
        assert [(e["pos"], e["pad"], e["dur"], e["note"], e["byte7"]) for e in notes] == \
               [(e["tick"], e["pad"], e["duration"], e["note"], e["byte7"]) for e in pattern["events"]]
        assert [(e["pos"], e["param"], e["value"]) for e in params] == \
               [(e["tick"], e["param"], e["value"]) for e in pattern["automation"]]
    assert tool["settings"]["bpm"] == package["bpm"]
    assert tool["scenes"]["populated"] == []


def test_project_from_pak(tmp_path):
    tar = P.pack_project(minimal_project())
    pak = tmp_path / "x.pak"
    with zipfile.ZipFile(pak, "w") as z:
        z.writestr("/meta.json", json.dumps({"device_version": "2.5.1"}))
        z.writestr("/projects/P04.tar", tar)
    assert D.project_from_pak(pak.read_bytes(), 4) == tar
    with pytest.raises(ValueError):
        D.project_from_pak(pak.read_bytes(), 5)
