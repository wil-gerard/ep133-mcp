"""check_ppak: the import preflight, against synthesised .ppaks and a fake device."""

import json
from types import SimpleNamespace
import zipfile

import pytest

from ep133_mcp.protocol import generate as G
from ep133_mcp.protocol import projects as P
from ep133_mcp.safety.errors import InvalidDestination
from ep133_mcp.safety.import_project import check_ppak, inspect_ppak
from test_install import FakeDevice
from test_pack_project import minimal_project


def ppak(tmp_path, project=7, files=None, sounds=None, meta=None, name="x.ppak"):
    tar = P.pack_project(files or minimal_project())
    meta = P.project_meta({"device_version": "2.5.1", **(meta or {})})
    data = P.build_ppak(project, tar, meta, sounds)
    path = tmp_path / name
    path.write_bytes(data)
    return path


def wav(frames=100):
    import io
    import wave
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setparams((1, 2, 46875, frames, "NONE", "not compressed"))
        w.writeframes(bytes(frames * 2))
    return out.getvalue()


class Device(FakeDevice):
    def __init__(self):
        super().__init__()
        self.active = 3
        self.pads[7, "A", 7] = (16, 500)

    def greet(self):
        return SimpleNamespace(sku="TE032AS001", os_version="2.5.1", serial="x")

    def active_project(self):
        return self.active


def test_inspect_reads_a_good_export(tmp_path):
    path = ppak(tmp_path, sounds={"/sounds/016 tone.wav": wav()})
    out = inspect_ppak(path, 7)
    assert out["problems"] == [] and out["entry"] == "/projects/P07.tar"
    assert out["referenced_slots"] == [16] and out["included_sounds"] == {16: {"name": "tone", "frames": 100, "channels": 1, "samplerate": 46875}}
    assert out["contents"]["bpm"] == 120.0
    assert out["contents"]["pads_assigned"] == [{"group": "A", "pad": 7, "slot": 16, "frames": 37500}]
    assert [p["index"] for p in out["contents"]["patterns"]] == [1, 3]


def test_inspect_names_each_problem(tmp_path):
    path = ppak(tmp_path)
    assert "not P05" in inspect_ppak(path, 5)["problems"][0]
    full = tmp_path / "full.pak"
    with zipfile.ZipFile(full, "w") as z:
        z.writestr("/meta.json", json.dumps({"pak_type": "user", "device_version": "2.5.1"}))
        for n in range(1, 10):
            z.writestr(f"/projects/P{n:02}.tar", P.pack_project(minimal_project()))
    problems = inspect_ppak(full, 7)["problems"]
    assert any("pak_type" in p for p in problems) and any("holds project(s)" in p for p in problems)
    # a TAR in another flavour (Python's own tar writer)
    import io
    import tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        for name, data in minimal_project().items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    odd = tmp_path / "odd.ppak"
    with zipfile.ZipFile(odd, "w") as z:
        z.writestr("/meta.json", json.dumps({"pak_type": "project", "device_version": "2.5.1"}))
        z.writestr("/projects/P07.tar", buf.getvalue())
    assert any("flavour" in p for p in inspect_ppak(odd, 7)["problems"])
    bad_sound = ppak(tmp_path, sounds={"/sounds/016 tone.wav": b"not a wav"}, name="bad.ppak")
    assert any("WAV" in p for p in inspect_ppak(bad_sound, 7)["problems"])
    for path, project in ((tmp_path / "missing.ppak", 7), (path, 0), (path, "7")):
        with pytest.raises(InvalidDestination):
            inspect_ppak(path, project)
    (tmp_path / "junk.ppak").write_bytes(b"junk")
    with pytest.raises(InvalidDestination):
        inspect_ppak(tmp_path / "junk.ppak", 7)


def test_check_against_the_device(tmp_path):
    d = Device()
    out = check_ppak(ppak(tmp_path), 7, d)
    assert out["status"] == "ok" and out["device"]["active_project"] == 3
    assert out["slots"] == {"on_device": [16], "absent_from_device": [], "absent_and_not_included": [],
                            "included_but_already_on_device": []}
    assert out["would_replace"] == {"bpm": None, "pads_assigned": 1, "patterns": 0, "events": 0, "scenes": 0}
    assert "project 7" in out["instruction"]


def test_check_flags_active_project_sku_and_slots(tmp_path):
    d = Device()
    d.active = 7
    d.slots = set()
    out = check_ppak(ppak(tmp_path, meta={"device_sku": "TE032AS999"}), 7, d)
    assert out["status"] == "problems"
    text = " ".join(out["problems"])
    assert "active project" in text and "SKU" in text and "neither on the device nor in the file: [16]" in text
    assert out["slots"]["absent_and_not_included"] == [16]
    d.slots = {16}
    out = check_ppak(ppak(tmp_path, sounds={"/sounds/016 tone.wav": wav()}, name="y.ppak"), 7, d)
    assert out["slots"]["included_but_already_on_device"] == [16] and any("overwrite" in p for p in out["problems"])


def test_generate_ppak_output_passes_check(tmp_path):
    files = minimal_project()
    tar = P.pack_project(files)
    out = tmp_path / "gen.ppak"
    G.generate_ppak(tar, 7, out, {"device_version": "2.5.1"}, bpm=99.0,
                    patterns=[{"group": "A", "index": 2, "bars": 1, "steps": {"1": "x...x...x...x..."}}])
    report = check_ppak(out, 7, Device())
    assert report["status"] == "ok" and report["contents"]["bpm"] == 99.0
    assert {(p["group"], p["index"]) for p in report["contents"]["patterns"]} == {("A", 1), ("A", 2), ("B", 3)}
