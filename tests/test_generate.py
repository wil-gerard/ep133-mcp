"""generate_ppak: patch a synthesized device-flavour project and read every change back.

The acceptance chain runs the real transcribe_groove output (fixture clip ->
kit -> groove) through generate_ppak, then tools/pattern_decode.py check on the
.ppak and a decode of the written pattern back to the input strings.
"""

import io
import json
import struct
import sys
from pathlib import Path

import pytest

from ep133_mcp.protocol import generate as G, patterns as enc, projects as P
from test_pack_project import minimal_project

sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))
import pattern_decode  # noqa: E402

META = {"pak_type": "user", "device_version": "2.5.1", "device_sku": "TE032AS001"}
GROOVE = {"1": "x.......x.......x.......x.......", "2": "....x.......x.......x.......x...",
          "3": "..x...x...x...x...x...x...x...x."}


def template() -> bytes:
    return P.pack_project(minimal_project())


def test_pattern_encoding_round_trip():
    data = enc.encode_pattern(2, {1: GROOVE["1"], 2: GROOVE["2"], 3: GROOVE["3"]})
    assert data[:4] == bytes([0, 2, 16, 0]) and len(data) == 4 + 8 * 16
    first = struct.unpack_from("<HBBBHB", data, 4)
    assert first == (0, 0, 60, 100, 24, 0)                        # tick 0, pad 1, note 60, byte4 100, one 16th, byte7 0
    assert struct.unpack_from("<HBBBHB", data, 12)[:2] == (48, 2 << 3)  # tick 48 = step 2, pad 3
    assert enc.pattern_steps(data) == {1: GROOVE["1"], 2: GROOVE["2"], 3: GROOVE["3"]}
    assert enc.encode_pattern(1, {5: "o..............."}) == enc.encode_pattern(1, {5: "x..............."})
    decoded = pattern_decode.decode_pattern(data)
    assert decoded["bars"] == 2 and decoded["count"] == 16
    assert [e["pos"] for e in decoded["events"]] == sorted(e["pos"] for e in decoded["events"])


@pytest.mark.parametrize("bars,steps", [(0, {1: ""}), (16, {1: "x" * 256}), (1, {0: "x" * 16}), (1, {13: "x" * 16}),
                                        (1, {1: "x" * 15}), (1, {1: "xq" + "." * 14}), (2, {p: "x" * 32 for p in range(1, 13)}),
                                        (2, {1: "x" * 16})])
def test_pattern_encoding_rejects(bars, steps):
    with pytest.raises(ValueError):
        enc.encode_pattern(bars, steps)


def test_scene_bpm_and_pad_patches():
    scenes = bytes(712)
    patched = enc.patch_scene(scenes, 2, {"A": 1, "B": 3, "C": 1, "D": 1})
    assert patched[13:19] == bytes([1, 3, 1, 1, 4, 4]) and patched[:13] == bytes(13) and len(patched) == 712
    assert enc.decode_scene(patched, 2) == {"A": 1, "B": 3, "C": 1, "D": 1, "num": 4, "den": 4}
    for bad in ({"A": 0, "B": 1, "C": 1, "D": 1}, {"A": 1, "B": 1, "C": 1}, {"A": 100, "B": 1, "C": 1, "D": 1}):
        with pytest.raises(ValueError):
            enc.patch_scene(scenes, 1, bad)
    with pytest.raises(ValueError):
        enc.patch_scene(bytes(700), 1, {"A": 1, "B": 1, "C": 1, "D": 1})
    for size in enc.SETTINGS_SIZES:                     # the device writes 222 and 224; 220 seen upstream
        settings = enc.patch_bpm(bytes(size), 97.5)
        assert enc.decode_bpm(settings) == pytest.approx(97.5) and settings[8:] == bytes(size - 8)
    for bad in (10, 500, "120", True):
        with pytest.raises(ValueError):
            enc.patch_bpm(bytes(222), bad)
    with pytest.raises(ValueError):
        enc.patch_bpm(bytes(221), 97.5)
    record = enc.patch_pad_record(bytes(range(27)), 16, 18750)
    assert struct.unpack_from("<H", record, 1)[0] == 16 and struct.unpack_from("<I", record, 8)[0] == 18750
    assert record[0] == 0 and record[3:8] == bytes(range(3, 8)) and record[12:] == bytes(range(12, 27))
    with pytest.raises(ValueError):
        enc.patch_pad_record(bytes(27), 0, 10)


def test_patch_project_manifest():
    tar, manifest = G.patch_project(template(), bpm=97.0,
                                    pads=[{"group": "A", "pad": 1, "slot": 20, "frames": 4000}],
                                    patterns=[{"group": "A", "index": 1, "bars": 2, "steps": GROOVE},
                                              {"group": "B", "index": 9, "bars": 1, "steps": {"4": "x" * 16}}],
                                    scenes=[{"scene": 1, "A": 1, "B": 9, "C": 1, "D": 1}])
    files = P.unpack_project(tar)
    assert P.pack_project(files) == tar
    by_member = {m["member"]: m for m in manifest}
    assert set(by_member) == {"settings", "pads/a/p01", "patterns/a01", "patterns/b09", "scenes"}
    assert by_member["settings"]["ranges"] == [{"offset": 6, "length": 1, "before": "f0", "after": "c2"}]
    assert by_member["pads/a/p01"]["ranges"] == [{"offset": 1, "length": 1, "before": "00", "after": "14"},
                                                {"offset": 8, "length": 2, "before": "0000", "after": "a00f"}]
    assert by_member["patterns/a01"] == {"member": "patterns/a01", "action": "replaced", "bytes_before": 12, "bytes": 132,
                                         "events_dropped": 1, "parameter_events_dropped": 0}
    assert by_member["patterns/b09"] == {"member": "patterns/b09", "action": "added", "bytes": 132}
    assert by_member["scenes"]["ranges"] == [{"offset": 7, "length": 6, "before": "000000000000", "after": "010901010404"}]
    assert enc.pattern_steps(files["patterns/a01"]) == {1: GROOVE["1"], 2: GROOVE["2"], 3: GROOVE["3"]}
    assert files["fx_settings"] == bytes(160) and files["patterns/b03"] == bytes([0, 2, 0, 0])
    assert P.stored_pads(tar)[0]["stored_slot"] == 20


@pytest.mark.parametrize("kwargs", [
    {"pads": [{"group": "A", "pad": 1, "slot": 20}]},
    {"pads": [{"group": "E", "pad": 1, "slot": 20, "frames": 1}]},
    {"patterns": [{"group": "A", "index": 1, "bars": 2, "steps": GROOVE, "song": 1}]},
    {"patterns": [{"group": "A", "index": 1, "bars": 2, "steps": {}}]},
    {"patterns": [{"group": "A", "index": 1, "bars": 2, "steps": GROOVE}, {"group": "A", "index": 1, "bars": 2, "steps": GROOVE}]},
    {"scenes": [{"scene": 1, "A": 1, "B": 0, "C": 1, "D": 1}]},
    {"scenes": [{"scene": 1, "A": 1, "B": 1, "C": 1}]},
    {"bpm": 1000},
])
def test_patch_project_rejects(kwargs):
    with pytest.raises(G.GenerateError):
        G.patch_project(template(), **kwargs)


def test_patch_project_needs_device_flavour_template():
    from test_pack_project import stdlib_tar
    with pytest.raises(G.GenerateError):
        G.patch_project(stdlib_tar(minimal_project()))
    with pytest.raises(G.GenerateError):
        G.patch_project(template()[:-512])


def test_generate_ppak_writes_and_refuses_overwrite(tmp_path):
    out = tmp_path / "song.ppak"
    result = G.generate_ppak(template(), 5, out, META, patterns=[{"group": "A", "index": 2, "bars": 2, "steps": GROOVE}],
                             sounds={"/sounds/016 tone.wav": b"RIFF", "/sounds/017 x.wav": b"RIFF"})
    assert result["status"] == "written" and result["verified_on_device"] is False and result["velocity"] is None
    assert result["patterns_written"] == {"patterns/a02": GROOVE}
    assert result["sounds_included"] == ["/sounds/016 tone.wav"]
    meta, projects, sounds = P.read_pak(out.read_bytes())
    assert meta["pak_type"] == "project" and meta["device_version"] == "2.5.1"
    assert list(projects) == [5] and list(sounds) == ["/sounds/016 tone.wav"]
    assert pattern_decode.cmd_check(pattern_decode.load_projects(str(out))) == 0
    with pytest.raises(G.GenerateError):
        G.generate_ppak(template(), 5, out, META, bpm=100.0)
    with pytest.raises(G.GenerateError):
        G.generate_ppak(template(), 5, tmp_path / "other.ppak", META)
    with pytest.raises(G.GenerateError):
        G.generate_ppak(template(), 5, tmp_path / "other.pak", META, bpm=100.0)
    soft = G.generate_ppak(template(), 5, tmp_path / "soft.ppak", META,
                           patterns=[{"group": "A", "index": 1, "bars": 1, "steps": {"1": "o..............."}}])
    assert "velocity" in soft["velocity"]


def test_template_from_pak(tmp_path):
    import zipfile
    pak = tmp_path / "b.pak"
    with zipfile.ZipFile(pak, "w") as z:
        z.writestr("/meta.json", json.dumps(META))
        z.writestr("/projects/P03.tar", template())
    tar, meta, sounds = G.template_from_pak(pak, 3)
    assert tar == template() and meta == META and sounds == {}
    with pytest.raises(G.GenerateError):
        G.template_from_pak(pak, 4)
    with pytest.raises(G.GenerateError):
        G.template_from_pak(tmp_path / "missing.pak", 3)


def test_acceptance_fixture_groove_to_ppak(tmp_path, capsys):
    """Fixture clip -> extract_kit -> transcribe_groove -> generate_ppak -> check -> decode equals input."""
    pytest.importorskip("librosa", reason="audio extra not installed")
    import synth_reference
    from ep133_mcp.audio import groove, kit

    truth = synth_reference.write(tmp_path / "ref")
    kit.extract_kit(truth["clip"], separation="hpss", beat_tracker="librosa")
    record = groove.transcribe_groove(truth["clip"], downbeat_s=0.0, group="B", index=2,
                                      separation="hpss", beat_tracker="librosa")
    out = tmp_path / "groove.ppak"
    result = G.generate_ppak(template(), 5, out, META, bpm=record["bpm"], patterns=[record["pattern"]],
                             scenes=[{"scene": 1, "A": 1, "B": 2, "C": 1, "D": 1}])
    assert result["patterns_written"]["patterns/b02"] == record["pattern"]["steps"]
    assert pattern_decode.cmd_check(pattern_decode.load_projects(str(out))) == 0
    assert "failures: 0" in capsys.readouterr().out
    files = P.unpack_project(P.read_pak(out.read_bytes())[1][5])
    assert enc.decode_bpm(files["settings"]) == pytest.approx(record["bpm"])
    assert enc.decode_scene(files["scenes"], 1) == {"A": 1, "B": 2, "C": 1, "D": 1, "num": 4, "den": 4}
    decoded = pattern_decode.decode_pattern(files["patterns/b02"])
    assert decoded["bars"] == record["bars"] and all(e["note"] == 60 and e["vel"] == 100 and e["dur"] == 24 for e in decoded["events"])


def test_add_events_keeps_existing_bytes():
    existing = bytes([0, 1, 2, 0]) + struct.pack("<HBBBHB", 48, (3 - 1) << 3, 62, 100, 30, 6) + struct.pack("<HBBBHB", 96, 0, 60, 100, 24, 8)
    grown = enc.add_events(existing, [(7, 2), (1, 0)])
    assert grown[:4] == bytes([0, 1, 4, 0]) and len(grown) == 4 + 8 * 4
    events = enc.decode_pattern(grown)["events"]
    assert [(e["pos"], e["pad"], e["byte7"]) for e in events] == [(0, 1, 0), (48, 3, 6), (48, 7, 0), (96, 1, 8)]
    assert existing[4:12] in grown and existing[12:20] in grown            # device-written events untouched, byte 7 included
    for bad in ([(0, 0)], [(13, 0)], [(1, 16)], [(1, -1)]):
        with pytest.raises(ValueError):
            enc.add_events(existing, bad)
    with pytest.raises(ValueError):
        enc.add_events(enc.encode_pattern(2, {p: "x" * 32 for p in range(1, 8)}) , [(1, 0)] * 40)


def test_patch_project_add_form():
    files = minimal_project()
    files["patterns/a01"] = bytes([0, 1, 1, 0]) + struct.pack("<HBBBHB", 0, 6 << 3, 60, 100, 24, 6)
    tar, manifest = G.patch_project(P.pack_project(files), patterns=[{"group": "A", "index": 1, "add": [{"pad": 7, "step": 8}]}])
    assert manifest == [{"member": "patterns/a01", "action": "extended", "bytes_before": 12, "bytes": 20, "events_added": 1,
                         "ranges": [{"offset": 2, "length": 1, "before": "01", "after": "02"}]}]
    grown = P.unpack_project(tar)["patterns/a01"]
    assert grown[:4] == bytes([0, 1, 2, 0]) and grown[4:12] == files["patterns/a01"][4:]
    assert grown[12:] == struct.pack("<HBBBHB", 192, 6 << 3, 60, 100, 24, 0)
    for bad in ([{"group": "A", "index": 9, "add": [{"pad": 7, "step": 8}]}],
                [{"group": "A", "index": 1, "add": []}],
                [{"group": "A", "index": 1, "add": [{"pad": 7}]}],
                [{"group": "A", "index": 1, "bars": 1, "add": [{"pad": 7, "step": 8}]}],
                [{"group": "A", "index": 1, "add": [{"pad": 7, "step": 8}]}, {"group": "A", "index": 1, "bars": 1, "steps": {"1": "x" * 16}}]):
        with pytest.raises(G.GenerateError):
            G.patch_project(P.pack_project(files), patterns=bad)


def test_generate_ppak_add_form_response(tmp_path):
    files = minimal_project()
    files["patterns/a01"] = bytes([0, 1, 1, 0]) + struct.pack("<HBBBHB", 0, 6 << 3, 60, 100, 24, 6)
    result = G.generate_ppak(P.pack_project(files), 5, tmp_path / "add.ppak", META,
                             patterns=[{"group": "A", "index": 1, "add": [{"pad": 7, "step": 8}]}])
    assert result["patterns_written"] == {"patterns/a01": {"events": 2, "added": [{"pad": 7, "step": 8}]}}
    assert result["velocity"] is None
    assert pattern_decode.cmd_check(pattern_decode.load_projects(str(tmp_path / "add.ppak"))) == 0
