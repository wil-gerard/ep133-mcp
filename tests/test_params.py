"""set_pad / set_slot against a fake device that merges JSON the way upstream documents.

The interesting cases are what the device does *after* an ok status: stores a clamped value,
drops a key, or couples another field. Every one must show in the result, never be assumed away.
"""

import copy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ep133_mcp.device import DeviceError
from ep133_mcp.safety.errors import InvalidDestination
from ep133_mcp.safety.journal import Journal
from ep133_mcp.safety.params import PAD_FIELDS, ParamWriter, SLOT_FIELDS, compare, validate_params
from test_install import FakeDevice

PAD = {"sym": 16, "sound.playmode": "oneshot", "sample.start": 0, "sample.end": 18750, "envelope.attack": 0,
       "envelope.release": 255, "sound.pitch": 0.0, "sound.amplitude": 100, "sound.pan": 0,
       "sound.mutegroup": False, "time.mode": "off", "midi.channel": 0}
SLOT = {"channels": 1, "samplerate": 46875, "format": "s16", "crc": 2727906176, "name": "16_testtone",
        "sample.start": 0, "sample.end": 18750, "sound.playmode": "oneshot", "sound.amplitude": 100,
        "sound.pan": 0, "sound.pitch": 0.0, "sound.rootnote": 60, "time.mode": "off", "sound.bpm": 0.0,
        "sound.bars": 1.0, "envelope.attack": 0, "envelope.release": 255, "sound.loopstart": -1,
        "sound.loopend": -1}


class ParamDevice(FakeDevice):
    def __init__(self):
        super().__init__()
        self.records = {3207: dict(PAD), 16: dict(SLOT)}
        self.samples = {16: {"crc": SLOT["crc"], "sample.end": 18750}}
        self.pads[1, "A", 7] = (16, 18750)
        self.drop = set()          # keys the device silently discards
        self.clamp = {}            # key -> value the device stores instead
        self.couple = {}           # key -> {other key: value} the device changes alongside
        self.fail = False

    def metadata(self, file_id):
        if file_id == 0:
            return {}
        if file_id in self.records:
            return copy.deepcopy(self.records[file_id])
        return None

    def read_pad(self, project, group, pad):
        node = 2000 + 1000 * project + 200 + 100 * "ABCD".index(group) + pad
        record = copy.deepcopy(self.records[node])
        sym = record["sym"]
        return {"project": project, "group": group, "pad": pad, "node": node, "sym": sym,
                "pad_metadata": record, "slot_metadata": copy.deepcopy(self.records.get(sym)) if sym else None}

    def set_metadata(self, file_id, fields):
        self.writes.append(("set", file_id, dict(fields)))
        if self.fail:
            raise DeviceError("write rejected")
        record = self.records[file_id]
        for key, value in fields.items():
            if key in self.drop:
                record.pop(key, None)
                continue
            record[key] = self.clamp.get(key, value)
        for key in fields:
            for other, coupled in self.couple.get(key, {}).items():
                record[other] = coupled


@pytest.fixture
def setup(tmp_path):
    from ep133_mcp.safety.backup import snapshot
    d = ParamDevice()
    backups = SimpleNamespace(require_current=Mock(side_effect=lambda *_: snapshot(d)), invalidate=Mock())
    return d, ParamWriter(backups, Journal(tmp_path / "journal"))


def approve(writer, device, method, *args):
    written = len(device.writes)
    first = getattr(writer, method)(*args, "backup", device)
    assert first["status"] == "needs_confirmation"
    assert len(device.writes) == written
    return getattr(writer, method)(*args, "backup", device, first["confirm"])


def test_validate_params_rules():
    assert validate_params({"sound.playmode": "key"}, PAD_FIELDS, "x") == {"sound.playmode": "key", "envelope.release": 15}
    assert validate_params({"sound.playmode": "oneshot", "envelope.release": 40}, PAD_FIELDS, "x") == \
        {"sound.playmode": "oneshot", "envelope.release": 40}
    assert validate_params({"sound.playmode": "legato"}, PAD_FIELDS, "x") == {"sound.playmode": "legato"}
    assert validate_params({"sound.pitch": 3}, PAD_FIELDS, "x") == {"sound.pitch": 3.0}
    for bad in ({}, None, {"sym": 5}, {"sound.playmode": 1}, {"sound.playmode": "loop"}, {"envelope.attack": 256},
                {"sound.pitch": 13}, {"sound.pitch": True}, {"sound.pan": -17}, {"sound.mutegroup": 1},
                {"midi.channel": 16}, {"sample.start": 100, "sample.end": 100}, {"sample.end": 0},
                {"sound.bpm": 240}, {"time.mode": "BPM"}):
        with pytest.raises(InvalidDestination):
            validate_params(bad, PAD_FIELDS, "x")
    assert validate_params({"name": "kick 2", "sound.loopend": -1}, SLOT_FIELDS, "x") == {"name": "kick 2", "sound.loopend": -1}
    for bad in ({"name": ""}, {"name": "x" * 21}, {"name": "é"}, {"sample.start": 0}, {"midi.channel": 1}):
        with pytest.raises(InvalidDestination):
            validate_params(bad, SLOT_FIELDS, "x")


def test_compare_reports_each_outcome():
    out = compare({"a": 1, "b": 2.0, "c": "x"}, {"a": 0, "b": 0, "c": "x", "d": 5}, {"a": 1, "b": 3.0, "d": 6})
    assert out == {"applied": {"a": 1}, "changed": {"b": {"requested": 2.0, "stored": 3.0}}, "dropped": ["c"],
                   "side_effects": {"d": {"before": 5, "after": 6}}}
    assert compare({"m": False}, {"m": False}, {"m": 0})["changed"] == {"m": {"requested": False, "stored": 0}}


def test_set_pad_confirms_then_writes_only_requested_fields(setup):
    d, writer = setup
    first = writer.set_pad(1, "A", 7, {"sound.pitch": -2, "sound.playmode": "key"}, "backup", d)
    assert first["impact"]["before"] == {"sound.pitch": 0.0, "sound.playmode": "oneshot", "envelope.release": 255}
    assert first["impact"]["after"] == {"sound.pitch": -2.0, "sound.playmode": "key", "envelope.release": 15}
    assert first["impact"]["auto_paired"] == {"envelope.release": 15}
    out = writer.set_pad(1, "A", 7, {"sound.pitch": -2, "sound.playmode": "key"}, "backup", d, first["confirm"])
    assert out["status"] == "written" and out["node"] == 3207 and out["sym"] == 16
    assert d.writes == [("set", 3207, {"sound.pitch": -2.0, "sound.playmode": "key", "envelope.release": 15})]
    assert out["applied"] == {"sound.pitch": -2.0, "sound.playmode": "key", "envelope.release": 15}
    assert out["changed"] == {} and out["dropped"] == [] and out["side_effects"] == {}
    assert out["power_cycle_verified"] is False and "before_record" not in out
    writer.backups.invalidate.assert_called_once()


def test_set_pad_reports_clamped_dropped_and_coupled(setup):
    d, writer = setup
    d.clamp["sound.bars"] = 4.0
    d.drop.add("midi.channel")
    d.couple["sound.playmode"] = {"envelope.release": 255}
    out = approve(writer, d, "set_pad", 1, "A", 7, {"sound.bars": 3, "midi.channel": 4, "sound.playmode": "key"})
    assert out["status"] == "written_with_differences"
    assert out["changed"] == {"sound.bars": {"requested": 3.0, "stored": 4.0},
                              "envelope.release": {"requested": 15, "stored": 255}}
    assert out["dropped"] == ["midi.channel"]
    assert out["applied"] == {"sound.playmode": "key"}


def test_set_pad_refusals(setup):
    d, writer = setup
    with pytest.raises(InvalidDestination):
        writer.set_pad(1, "A", 7, {"sample.end": 20000}, "backup", d)      # past the sample
    d.records[3208] = {"sym": 0}
    with pytest.raises(InvalidDestination):
        writer.set_pad(1, "A", 8, {"sound.pitch": 1}, "backup", d)         # empty pad
    with pytest.raises(InvalidDestination):
        writer.set_pad(1, "E", 7, {"sound.pitch": 1}, "backup", d)
    assert d.writes == []


def test_token_is_bound_to_the_impact(setup):
    d, writer = setup
    first = writer.set_pad(1, "A", 7, {"sound.pan": 4}, "backup", d)
    again = writer.set_pad(1, "A", 7, {"sound.pan": 5}, "backup", d, first["confirm"])
    assert again["status"] == "needs_confirmation" and d.writes == []
    d.records[3207]["sound.pan"] = 2                                       # device changed under us
    third = writer.set_pad(1, "A", 7, {"sound.pan": 4}, "backup", d, first["confirm"])
    assert third["status"] == "needs_confirmation" and d.writes == []


def test_write_failure_is_journalled_and_undo_declines(setup):
    d, writer = setup
    d.fail = True
    out = approve(writer, d, "set_pad", 1, "A", 7, {"sound.pan": 4})
    assert out["status"] == "failed" and out["failure"]["error"] == "DeviceError"
    d.fail = False
    assert writer.undo(d)["status"] == "partial"


def test_undo_writes_previous_values_back(setup):
    d, writer = setup
    approve(writer, d, "set_pad", 1, "A", 7, {"sound.pitch": 5, "sound.playmode": "key"})
    assert d.records[3207]["sound.pitch"] == 5.0
    out = writer.undo(d)
    assert out["status"] == "undone" and out["kind"] == "pad" and out["file_id"] == 3207
    assert d.writes[-1] == ("set", 3207, {"sound.pitch": 0.0, "sound.playmode": "oneshot", "envelope.release": 255})
    assert d.records[3207]["sound.pitch"] == 0.0 and out["not_restorable"] == []
    assert writer.undo(d)["status"] == "undone"                            # idempotent


def test_undo_cannot_unset_a_key_that_was_absent(setup):
    d, writer = setup
    del d.records[3207]["midi.channel"]
    approve(writer, d, "set_pad", 1, "A", 7, {"midi.channel": 3, "sound.pan": 1})
    out = writer.undo(d)
    assert out["status"] == "undone" and out["not_restorable"] == ["midi.channel"]
    assert d.writes[-1] == ("set", 3207, {"sound.pan": 0})
    approve(writer, d, "set_pad", 1, "A", 7, {"midi.channel": 5})
    d.drop.add("midi.channel")                                             # device now discards the key
    out = writer.undo(d)
    assert out["status"] == "undo_partial" and out["dropped"] == ["midi.channel"]


def test_nothing_to_undo(setup):
    d, writer = setup
    assert writer.undo(d) == {"status": "nothing_to_undo"}


def test_set_slot_writes_and_reads_back(setup):
    d, writer = setup
    first = writer.set_slot(16, {"name": "kick", "sound.loopstart": 100, "sound.loopend": 9000}, "backup", d)
    assert first["impact"]["slot"] == 16 and first["impact"]["before"]["name"] == "16_testtone"
    out = writer.set_slot(16, {"name": "kick", "sound.loopstart": 100, "sound.loopend": 9000}, "backup", d, first["confirm"])
    assert out["status"] == "written" and out["kind"] == "slot"
    assert d.records[16]["name"] == "kick" and d.records[16]["sound.loopend"] == 9000
    undo = writer.undo(d)
    assert undo["status"] == "undone" and d.records[16]["name"] == "16_testtone"


def test_set_slot_refusals(setup):
    d, writer = setup
    for slot, params in ((0, {"name": "x"}), (17, {"name": "x"}), (16, {"sound.loopend": 99999}), (16, {"sym": 1})):
        with pytest.raises(InvalidDestination):
            writer.set_slot(slot, params, "backup", d)
    assert d.writes == []
