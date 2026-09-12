"""diff_project_floats: named settings / fx_settings fields, byte ranges for everything else."""

import struct

from ep133_mcp.protocol import diffmap as M
from ep133_mcp.protocol import projects as P
from test_pack_project import minimal_project


def with_settings(**changes):
    files = minimal_project()
    settings = bytearray(files["settings"])
    fx = bytearray(files["fx_settings"])
    for key, value in changes.items():
        member, at, fmt = key.split("_", 2)[0], int(key.split("_")[1]), key.split("_")[2]
        struct.pack_into("<" + fmt, settings if member == "s" else fx, at, value)
    files["settings"], files["fx_settings"] = bytes(settings), bytes(fx)
    return files


def test_identical():
    tar = P.pack_project(minimal_project())
    assert M.diff_project_floats(tar, tar) == {"settings": [], "fx_settings": [], "other": [], "identical": True}


def test_settings_fields_are_named():
    old = P.pack_project(minimal_project())
    new = P.pack_project(with_settings(s_4_f=99.0, s_92_f=235 / 256, s_218_B=6, s_221_B=8))   # params[17], group C
    out = M.diff_project_floats(old, new)
    assert out["identical"] is False and out["fx_settings"] == [] and out["other"] == []
    fields = {e["field"]: e for e in out["settings"]}
    assert fields["bpm"] == {"member": "settings", "field": "bpm", "offset": 4, "before": 120.0, "after": 99.0}
    param = fields["params[17]"]
    assert param["offset"] == 92 and param["group"] == "B" and param["function"] == 5
    assert param["before"] == 0.0 and param["after_n256"] == 235 and param["before_n256"] == 0
    assert fields["group_bytes[2] (group C)"]["after"] == 6
    unnamed = fields["unnamed"]
    assert unnamed["offset"] == 221 and unnamed["after"] == "08"


def test_fx_settings_fields_are_named():
    old = P.pack_project(minimal_project())
    new = P.pack_project(with_settings(f_4_B=6, f_16_f=0.5, f_150_B=1))       # selector, params[2], tail
    out = M.diff_project_floats(old, new)
    fields = {e["field"]: e for e in out["fx_settings"]}
    assert fields["selector"] == {"member": "fx_settings", "field": "selector", "offset": 4, "before": 0, "after": 6}
    assert fields["params[2]"]["after_n256"] == 128 and fields["params[2]"]["index"] == 2
    assert fields["unnamed"]["offset"] == 150
    assert out["settings"] == []


def test_other_members_as_ranges():
    files = minimal_project()
    old = P.pack_project(files)
    files["patterns/a01"] = files["patterns/a01"][:4] + bytes([3, 0]) + files["patterns/a01"][6:]
    files["pads/b/p02"] = bytes(26)
    del files["patterns/b03"]
    files["patterns/c04"] = bytes(4)
    new = P.pack_project(files)
    out = M.diff_project_floats(old, new)
    assert [(o["member"], o["change"]) for o in out["other"]] == [
        ("pads/b/p02", "resized"), ("patterns/a01", "patched"), ("patterns/b03", "removed"), ("patterns/c04", "added")]
    assert out["other"][1]["ranges"] == [{"offset": 4, "length": 1, "before": "00", "after": "03"}]


def test_n256():
    assert M._n256(235 / 256) == 235 and M._n256(-1.0) is None and M._n256(0.3333) is None and M._n256(1.0) == 256
