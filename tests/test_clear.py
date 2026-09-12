"""clear_pads: whole projects to sym 0, journalled, undoable while the slots exist."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ep133_mcp.device import DeviceError
from ep133_mcp.safety.clear import Clearer
from ep133_mcp.safety.errors import InvalidDestination
from ep133_mcp.safety.journal import Journal
from test_chop import ChopDevice


class ClearDevice(ChopDevice):
    def __init__(self):
        super().__init__()
        self.active = 3
        self.fail_node = None

    def active_project(self):
        return self.active

    def set_metadata(self, node, fields):
        if node == self.fail_node:
            self.writes.append(("set", node, dict(fields)))
            raise DeviceError("write rejected")
        super().set_metadata(node, fields)


@pytest.fixture
def setup(tmp_path):
    from ep133_mcp.safety.backup import snapshot
    d = ClearDevice()
    d.samples[16] = {"crc": 16, "sample.end": 500, "name": "kick"}
    d.samples[17] = {"crc": 17, "sample.end": 900, "name": "snare"}
    d.slots.add(17)
    d.pads[1, "A", 1] = (16, 500)
    d.pads[1, "A", 2] = (17, 900)
    d.records[d.node(1, "A", 2)] = {"sym": 17, "sample.start": 0, "sample.end": 900, "sound.playmode": "key"}
    d.pads[2, "B", 5] = (549, 0)                       # stale: stored slot absent, resolves empty
    d.pads[3, "A", 1] = (16, 500)                      # the project being kept (active)
    backups = SimpleNamespace(require_current=Mock(side_effect=lambda *_: snapshot(d)), invalidate=Mock())
    return d, Clearer(backups, Journal(tmp_path / "journal"))


def approve(clearer, d, projects):
    first = clearer.clear(projects, "backup", d)
    assert first["status"] == "needs_confirmation"
    return first, clearer.clear(projects, "backup", d, first["confirm"])


def test_impact_lists_every_stored_pad_and_nothing_is_written_before_confirm(setup):
    d, clearer = setup
    first = clearer.clear([1, 2], "backup", d)
    assert first["status"] == "needs_confirmation" and d.writes == []
    assert first["impact"]["pads"] == 3 and first["impact"]["live"] == 2 and first["impact"]["stale"] == 1
    assert first["impact"]["slots_freed_for_delete"] == [16, 17]
    assert first["impact"]["entries"] == [
        {"project": 1, "group": "A", "pad": 1, "prior_slot": 16, "name": "kick", "stale": False},
        {"project": 1, "group": "A", "pad": 2, "prior_slot": 17, "name": "snare", "stale": False},
        {"project": 2, "group": "B", "pad": 5, "prior_slot": 549, "name": None, "stale": True}]


def test_clear_writes_sym_zero_to_each_pad_and_reads_back(setup):
    d, clearer = setup
    _, out = approve(clearer, d, [1, 2])
    assert out["status"] == "cleared" and out["cleared"] == 3 and out["projects"] == [1, 2]
    assert [w for w in d.writes if w[0] == "set"] == [("set", d.node(1, "A", 1), {"sym": 0}),
                                                      ("set", d.node(1, "A", 2), {"sym": 0}),
                                                      ("set", d.node(2, "B", 5), {"sym": 0})]
    assert d.pads[1, "A", 1] == (0, 0) and d.pads[1, "A", 2] == (0, 0) and d.pads[2, "B", 5] == (0, 0)
    assert d.pads[3, "A", 1] == (16, 500)              # the kept project is untouched
    assert "before_record" not in out["entries"][1]


def test_refusals(setup):
    d, clearer = setup
    for projects in ([], [3], [1, 3], [0], [1, 1], "1"):
        with pytest.raises(InvalidDestination):
            clearer.clear(projects, "backup", d)
    assert d.writes == []
    assert clearer.clear([4], "backup", d) == {"status": "nothing_to_clear", "projects": [4]}


def test_clear_stops_at_the_first_failure(setup):
    d, clearer = setup
    d.fail_node = d.node(1, "A", 2)
    _, out = approve(clearer, d, [1, 2])
    assert out["status"] == "partial"
    assert [e["status"] for e in out["entries"]] == ["cleared", "failed", "pending"]
    assert out["entries"][1]["failure"]["error"] == "DeviceError"
    assert d.pads[2, "B", 5] == (549, 0)


def test_clear_reports_a_length_the_device_kept(setup):
    d, clearer = setup
    d.keep_length_on_clear = True
    _, out = approve(clearer, d, [1])
    assert out["status"] == "cleared"
    assert out["entries"][0]["note"].endswith("left stored length 500 in the project record")


def test_undo_repoints_pads_whose_slots_still_exist(setup):
    d, clearer = setup
    approve(clearer, d, [1, 2])
    d.slots.discard(17)                                # slot 17 deleted after the clear
    out = clearer.undo(d)
    assert out["status"] == "undone" and out["undone"] == 3
    assert d.pads[1, "A", 1] == (16, 500)
    assert d.pads[1, "A", 2] == (0, 0) and "prior slot 17 is absent" in out["entries"][1]["undo_note"]
    assert d.pads[2, "B", 5] == (0, 0) and "prior slot 549 is absent" in out["entries"][2]["undo_note"]
    assert [w for w in d.writes if w[0] == "set"][-1] == ("set", d.node(1, "A", 1), {"sym": 16})
    assert clearer.undo(d)["status"] == "undone"       # nothing left: reports, no writes


def test_undo_restores_trim_and_playmode_and_skips_changed_pads(setup):
    d, clearer = setup
    approve(clearer, d, [1])
    d.pads[1, "A", 1] = (16, 500)                      # re-assigned by hand since the clear
    out = clearer.undo(d)
    assert out["status"] == "undo_partial"
    assert out["entries"][0]["undo_failure"].startswith("Pad changed since the clear")
    assert [w for w in d.writes if w[0] == "set"][-1] == (
        "set", d.node(1, "A", 2), {"sym": 17, "sample.start": 0, "sample.end": 900, "sound.playmode": "key"})
    assert d.pads[1, "A", 2] == (17, 900)


def test_nothing_to_undo(setup):
    d, clearer = setup
    assert clearer.undo(d) == {"status": "nothing_to_undo"}
