"""chop_sample: slice planning offline, then the upload-assign-trim flow against a fake device."""

import copy
from types import SimpleNamespace
from unittest.mock import Mock
import wave

import pytest

from ep133_mcp.device import DeviceError
from ep133_mcp.safety.chop import Chopper, plan_slices
from ep133_mcp.safety.errors import InvalidDestination
from ep133_mcp.safety.journal import Journal
from test_install import FakeDevice
from test_preflight import wav_file


def test_equal_slices():
    ranges, detail = plan_slices(1000, {"mode": "equal", "count": 4}, 4)
    assert [(r["start"], r["end"]) for r in ranges] == [(0, 250), (250, 500), (500, 750), (750, 1000)]
    assert detail == {"mode": "equal", "count": 4}
    assert plan_slices(1000, {"mode": "equal"}, 3)[0][-1]["end"] == 1000
    for bad in ({"mode": "equal", "count": 3}, {"mode": "equal", "extra": 1}, {"mode": "weird"}, "equal", None):
        with pytest.raises(InvalidDestination):
            plan_slices(1000, bad, 4)
    with pytest.raises(InvalidDestination):
        plan_slices(5, {"mode": "equal"}, 4)                   # a slice under two frames


def test_explicit_slices():
    ranges, detail = plan_slices(46875, [{"start_s": 0, "end_s": 0.25}, {"start_s": 0.5, "end_s": 2.0}], 2)
    assert [(r["start"], r["end"]) for r in ranges] == [(0, 11719), (23438, 46875)]   # end clamped to the file
    assert ranges[1]["end_s"] == 1.0 and detail == {"mode": "explicit"}
    for bad in ([{"start_s": 0, "end_s": 0.2}], [{"start_s": 0.3, "end_s": 0.2}, {"start_s": 0.4, "end_s": 0.5}],
                [{"start_s": 0, "end_s": 0.3}, {"start_s": 0.2, "end_s": 0.5}], [{"start": 0, "end": 1}, {}],
                [{"start_s": -1, "end_s": 0.2}, {"start_s": 0.3, "end_s": 0.4}]):
        with pytest.raises(InvalidDestination):
            plan_slices(46875, bad, 2)


def test_onset_slices(monkeypatch):
    import ep133_mcp.safety.chop as chop
    detected = [{"start_s": t, "strength": k} for t, k in ((0.0, 0.5), (0.1, 1.0), (0.25, 0.2), (0.7, 0.9))]
    monkeypatch.setattr(chop, "detect_slice_onsets", lambda pcm: detected)
    ranges, detail = plan_slices(46875, {"mode": "onsets"}, 3, b"")
    assert [(r["start"], r["end"]) for r in ranges] == [(0, 4688), (4688, 11719), (11719, 46875)]
    assert detail["pick"] == "first" and detail["used_s"] == [0.0, 0.1, 0.25] and detail["unused_onsets"] == 1
    assert detail["detected_s"] == [0.0, 0.1, 0.25, 0.7] and detail["strength"] == [0.5, 1.0, 0.2, 0.9]
    with pytest.raises(InvalidDestination):
        plan_slices(46875, {"mode": "onsets"}, 5, b"")
    with pytest.raises(InvalidDestination):
        plan_slices(46875, {"mode": "onsets", "count": 2}, 2, b"")
    with pytest.raises(InvalidDestination):
        plan_slices(46875, {"mode": "onsets", "pick": "loudest"}, 2, b"")


def test_onset_picks_strongest_and_spread(monkeypatch):
    import ep133_mcp.safety.chop as chop
    detected = [{"start_s": t, "strength": k} for t, k in
                ((0.0, 0.5), (0.1, 1.0), (0.25, 0.2), (0.4, 0.3), (0.55, 0.8), (0.7, 0.9))]
    monkeypatch.setattr(chop, "detect_slice_onsets", lambda pcm: detected)
    _, strongest = plan_slices(46875, {"mode": "onsets", "pick": "strongest"}, 3, b"")
    assert strongest["used_s"] == [0.1, 0.55, 0.7]                    # loudest three, back in time order
    ranges, spread = plan_slices(46875, {"mode": "onsets", "pick": "spread"}, 3, b"")
    assert spread["used_s"] == [0.0, 0.25, 0.55]                      # every second onset of six
    assert ranges[-1]["end"] == 46875 and ranges[0]["start"] == 0
    _, first = plan_slices(46875, {"mode": "onsets", "pick": "first"}, 3, b"")
    assert first["used_s"] == [0.0, 0.1, 0.25]


def test_onset_detection_on_real_pcm(tmp_path):
    pytest.importorskip("librosa")
    import numpy as np
    from ep133_mcp.safety.chop import detect_slice_onsets
    sr = 46875
    y = np.zeros(sr, dtype=np.float32)
    for at in (0.1, 0.4, 0.7):
        i = int(at * sr)
        y[i:i + 2000] = np.sin(np.arange(2000) * 0.2) * np.linspace(1, 0, 2000)
    found = detect_slice_onsets((y * 32767).astype("<i2").tobytes())
    assert len(found) == 3 and all(abs(o["start_s"] - t) < 0.03 for o, t in zip(found, (0.1, 0.4, 0.7)))
    assert all(0 < o["strength"] <= 1 for o in found)


class ChopDevice(FakeDevice):
    def __init__(self):
        super().__init__()
        self.records = {}
        self.fail_trim = None
        self.drop = set()

    def node(self, project, group, pad):
        return 2000 + 1000 * project + 200 + 100 * "ABCD".index(group) + pad

    def read_pad(self, project, group, pad):
        node = self.node(project, group, pad)
        record = copy.deepcopy(self.records.setdefault(node, {"sym": self.pads[project, group, pad][0]}))
        return {"project": project, "group": group, "pad": pad, "node": node, "sym": record["sym"],
                "pad_metadata": record, "slot_metadata": None}

    def assign_pad(self, node, slot):
        self.set_metadata(node, {"sym": slot})

    def set_metadata(self, node, fields):
        self.writes.append(("set", node, dict(fields)))
        if node == self.fail_trim and "sample.end" in fields:
            raise DeviceError("trim rejected")
        record = self.records.setdefault(node, {"sym": 0})
        if "sym" in fields:
            super().assign_pad(node, fields["sym"])
            record.clear()
            record["sym"] = fields["sym"]
            if fields["sym"]:
                record["sample.start"], record["sample.end"] = 0, self.samples[fields["sym"]]["sample.end"]
        for key, value in fields.items():
            if key != "sym" and key not in self.drop:
                record[key] = value


@pytest.fixture
def setup(tmp_path):
    from ep133_mcp.safety.backup import snapshot
    d = ChopDevice()
    d.samples[16] = {"crc": 16, "sample.end": 500}
    backups = SimpleNamespace(require_current=Mock(side_effect=lambda *_: snapshot(d)), invalidate=Mock())
    return d, Chopper(backups, Journal(tmp_path / "journal")), str(wav_file(tmp_path, frames=1000))


def test_chop_uploads_once_and_trims_each_pad(setup):
    d, chopper, wav = setup
    out = chopper.chop(wav, 2, "B", [10, 11, 12, 7], {"mode": "equal", "count": 4}, "backup", d)
    assert out["status"] == "chopped" and out["slices"] == {"mode": "equal", "count": 4}
    upload, *pads = out["entries"]
    assert upload == {"kind": "upload", "slot": 1, "frames": 1000, "crc": upload["crc"], "status": "uploaded"}
    assert [(e["pad"], e["start"], e["end"], e["status"]) for e in pads] == \
        [(10, 0, 250, "chopped"), (11, 250, 500, "chopped"), (12, 500, 750, "chopped"), (7, 750, 1000, "chopped")]
    assert [w for w in d.writes if w[0] == "upload"] == [("upload", 1)]
    assert d.records[d.node(2, "B", 11)] == {"sym": 1, "sample.start": 250, "sample.end": 500,
                                             "sound.playmode": "oneshot", "envelope.release": 255}
    assert all(d.pads[2, "B", p] == (1, 1000) for p in (10, 11, 12, 7))
    assert pads[0]["applied"]["sample.end"] == 250 and pads[0]["side_effects"] == {}
    assert "before_record" not in pads[0] and out["power_cycle_verified"] is False


def test_chop_confirms_when_a_pad_is_occupied(setup):
    d, chopper, wav = setup
    d.pads[2, "B", 11] = (16, 500)
    first = chopper.chop(wav, 2, "B", [10, 11], {"mode": "equal"}, "backup", d, playmode="key")
    assert first["status"] == "needs_confirmation" and d.writes == []
    assert [p["destructive"] for p in first["impact"]["pads"]] == [False, True]
    assert first["impact"]["playmode"] == "key"
    out = chopper.chop(wav, 2, "B", [10, 11], {"mode": "equal"}, "backup", d, first["confirm"], playmode="key")
    assert out["status"] == "chopped"
    assert d.records[d.node(2, "B", 11)]["envelope.release"] == 15


def test_chop_reports_dropped_fields_and_stops_on_failure(setup):
    d, chopper, wav = setup
    d.drop.add("sound.playmode")
    d.fail_trim = d.node(3, "C", 2)
    out = chopper.chop(wav, 3, "C", [1, 2, 3], {"mode": "equal"}, "backup", d)
    assert out["status"] == "partial"
    statuses = [e["status"] for e in out["entries"]]
    assert statuses == ["uploaded", "chopped_with_differences", "failed", "pending"]
    assert out["entries"][1]["dropped"] == ["sound.playmode"]
    assert out["entries"][2]["failure"]["error"] == "DeviceError"
    assert d.pads[3, "C", 2] == (1, 1000) and d.pads[3, "C", 3] == (0, 0)


def test_chop_refusals(setup):
    d, chopper, wav = setup
    for pads, slices, kw in (([1, 1], {"mode": "equal"}, {}), ([], {"mode": "equal"}, {}), (list(range(1, 14)), {"mode": "equal"}, {}),
                             ([13], {"mode": "equal"}, {}), ([1], {"mode": "equal"}, {"playmode": "loop"}),
                             ([1, 2], [{"start_s": 0, "end_s": 0.01}], {})):
        with pytest.raises(InvalidDestination):
            chopper.chop(wav, 1, "A", pads, slices, "backup", d, **kw)
    assert d.writes == []


def test_undo_restores_every_pad_and_leaves_the_slot(setup):
    d, chopper, wav = setup
    d.pads[2, "B", 11] = (16, 500)
    d.records[d.node(2, "B", 11)] = {"sym": 16, "sample.start": 0, "sample.end": 500, "sound.playmode": "key"}
    first = chopper.chop(wav, 2, "B", [10, 11], {"mode": "equal"}, "backup", d)
    chopper.chop(wav, 2, "B", [10, 11], {"mode": "equal"}, "backup", d, first["confirm"])
    out = chopper.undo(d)
    assert out["status"] == "undone" and out["library_slot_left_in_place"] == 1
    assert d.pads[2, "B", 10] == (0, 0) and d.pads[2, "B", 11] == (16, 500)
    sets = [w for w in d.writes if w[0] == "set"]
    assert sets[-2] == ("set", d.node(2, "B", 11), {"sym": 16, "sample.start": 0, "sample.end": 500, "sound.playmode": "key"})
    assert sets[-1] == ("set", d.node(2, "B", 10), {"sym": 0})
    assert chopper.undo(d)["status"] == "undone"


def test_undo_clears_pads_whose_prior_slot_is_gone(setup):
    d, chopper, wav = setup
    d.pads[2, "B", 10] = (549, 0)                     # stale reference: stored slot absent, resolves empty
    chopper.chop(wav, 2, "B", [10, 11], {"mode": "equal"}, "backup", d)
    out = chopper.undo(d)
    assert out["status"] == "undone"
    assert "prior slot 549 is absent" in out["entries"][1]["undo_note"]
    assert d.pads[2, "B", 10] == (0, 0) and d.pads[2, "B", 11] == (0, 0)
    assert [w for w in d.writes if w[0] == "set"][-2:] == [("set", d.node(2, "B", 11), {"sym": 0}),
                                                           ("set", d.node(2, "B", 10), {"sym": 0})]


def test_undo_skips_pads_that_changed_since(setup):
    d, chopper, wav = setup
    chopper.chop(wav, 2, "B", [10, 11], {"mode": "equal"}, "backup", d)
    d.pads[2, "B", 10] = (16, 500)
    out = chopper.undo(d)
    assert out["status"] == "undo_partial"
    assert [e.get("undo_failure") is not None for e in out["entries"][1:]] == [True, False]
    assert d.pads[2, "B", 11] == (0, 0)


def test_nothing_to_undo(setup):
    d, chopper, _ = setup
    assert chopper.undo(d) == {"status": "nothing_to_undo"}
