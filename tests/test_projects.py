import io
import struct
import tarfile
from unittest.mock import Mock

import pytest

from ep133_mcp.device.session import DeviceSession, DeviceRejected, DeviceTimeout
from ep133_mcp.protocol import projects as P
from ep133_mcp.protocol.sysex import Response


def archive(missing=None):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w") as t:
        for group in "abcd":
            for pad in range(1, 13):
                name = f"pads/{group}/p{pad:02}"
                if name == missing:
                    continue
                record = bytearray(26)
                struct.pack_into("<H", record, 1, 16 if pad == 7 else 0)
                struct.pack_into("<I", record, 8, 100 if pad == 7 else 0)
                m = tarfile.TarInfo(name)
                m.size = len(record)
                t.addfile(m, io.BytesIO(record))
    return out.getvalue()


def response(payload=b"", status=0):
    return Response(0, 0, 5, status, payload)


@pytest.mark.parametrize("project", [0, 10, 99, -1, True, 1.5])
def test_bad_project_never_touches_device(project):
    d = DeviceSession()
    d.request = Mock()
    with pytest.raises(ValueError):
        d.project_tar(project)
    d.request.assert_not_called()


def test_page_index_is_u16_after_status():
    assert P.page_data(b"\x01\x00abc", 256) == b"abc"
    for payload in (b"", b"\0", b"\0\1abc", bytes(327)):
        with pytest.raises(ValueError):
            P.page_data(payload, 0)


def test_stored_records_and_missing_pad():
    pads = P.stored_pads(archive())
    assert len(pads) == 48
    assert pads[6]["stored_slot"] == 16
    assert pads[6]["stored_length"] == 100
    with pytest.raises(ValueError, match="missing"):
        P.stored_pads(archive("pads/a/p01"))
    with pytest.raises(ValueError, match="incomplete"):
        P.stored_pads(archive()[:-1])


def test_timeout_is_not_eof():
    d = DeviceSession()
    d.greet = Mock()
    d.begin_read = Mock()
    d.request = Mock(side_effect=[response(), response(b"\0\0" + bytes(324)), DeviceTimeout("timeout")])
    with pytest.raises(DeviceTimeout):
        d.project_tar(1)


def test_project_transfer_and_rejection():
    d = DeviceSession()
    d.greet = Mock()
    d.begin_read = Mock()
    d.request = Mock(side_effect=[response(), response(b"\0\0" + bytes(324)), response(b"\0\1end")])
    assert d.project_tar(1) == bytes(324) + b"end"
    d.request = Mock(return_value=response(b"bad", 1))
    with pytest.raises(DeviceRejected):
        d.project_tar(1)
    assert d.request.call_count == 1


def test_unknown_slot_error_is_not_absence():
    d = DeviceSession()
    d.request = Mock(return_value=response(b"busy", 1))
    with pytest.raises(DeviceRejected):
        d.slot_exists(16)
    d.request = Mock(return_value=response(b"invalid file id\0", 1))
    assert d.slot_exists(16) is False
    d.request = Mock(return_value=response(b"not JSON"))
    assert d.slot_exists(16) is True


def test_stale_slot_is_visible_despite_zero_sym():
    d = DeviceSession()
    d.project_tar = Mock(return_value=archive())
    d.begin_read = Mock()
    d.slot_exists = Mock(return_value=False)
    d.pad_metadata = Mock(return_value={"sym": 0, "sound.playmode": "oneshot"})
    pads = d.list_pads(1)["pads"]
    assert pads[6]["stale_reference"] is True
    assert pads[6]["stored_slot"] == 16 and pads[6]["sym"] == 0
    assert pads[0]["stale_reference"] is False
    assert "metadata" not in pads[0]
    d.slot_exists.reset_mock()
    pads = d.list_pads(1, fields=True)["pads"]
    assert d.slot_exists.call_count == 2  # fresh existence checks on each call
    assert pads[0]["metadata"] == {"sym": 0, "sound.playmode": "oneshot"}
