from unittest.mock import Mock

import pytest

from ep133_mcp.device.session import DeviceSession, DeviceUnavailable, DeviceRejected
from ep133_mcp.protocol.sysex import Response


def test_ambiguous_endpoints_are_not_opened(monkeypatch):
    import mido
    monkeypatch.setattr(mido, 'get_output_names', lambda: ['EP-133 A', 'EP-133 B'])
    monkeypatch.setattr(mido, 'get_input_names', lambda: ['EP-133'])
    output = Mock()
    monkeypatch.setattr(mido, 'open_output', output)
    with pytest.raises(DeviceUnavailable):
        DeviceSession().open()
    output.assert_not_called()


def test_failed_input_closes_output_and_releases_lock(monkeypatch, tmp_path):
    import mido
    from pathlib import Path
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setattr(mido, 'get_output_names', lambda: ['EP-133'])
    monkeypatch.setattr(mido, 'get_input_names', lambda: ['EP-133'])
    output = Mock()
    monkeypatch.setattr(mido, 'open_output', Mock(return_value=output))
    monkeypatch.setattr(mido, 'open_input', Mock(side_effect=OSError('busy')))
    d = DeviceSession()
    with pytest.raises(DeviceUnavailable):
        d.open()
    output.close.assert_called_once()
    assert d._ownership is None


def test_second_session_refused_until_first_closes(monkeypatch, tmp_path):
    import mido
    from pathlib import Path
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setattr(mido, 'get_output_names', lambda: ['EP-133'])
    monkeypatch.setattr(mido, 'get_input_names', lambda: ['EP-133'])
    monkeypatch.setattr(mido, 'open_output', Mock(return_value=Mock()))
    monkeypatch.setattr(mido, 'open_input', Mock(return_value=Mock()))
    first = DeviceSession().open()
    try:
        with pytest.raises(DeviceUnavailable):
            DeviceSession().open()
    finally:
        first.close()
    DeviceSession().open().close()


def test_metadata_unknown_status_fails_and_non_object_is_occupied():
    d = DeviceSession()
    d.request = Mock(return_value=Response(0, 0, 5, 1, b'busy'))
    with pytest.raises(DeviceRejected):
        d.metadata(1)
    for payload in (b'\0\0[]\0', b'\0\0null\0'):
        d.request = Mock(return_value=Response(0, 0, 5, 0, payload))
        assert '_unparsed' in d.metadata(1)


def _pages(*documents):
    """Responses for successive metadata GETs: one page per document (each fits a page here)."""
    return [Response(0, 0, 5, 0, b'\0\0' + doc + b'\0') for doc in documents]


def test_read_pad_returns_pad_and_slot_json():
    d = DeviceSession()
    d.greet = Mock()
    d.begin_read = Mock()
    d.request = Mock(side_effect=_pages(b'{"sym":16,"sound.playmode":"oneshot","sample.end":18750}',
                                        b'{"name":"16_testtone","crc":2727906176,"sample.end":18750}'))
    out = d.read_pad(1, 'A', 7)
    assert out['node'] == 3207 and out['label'] == '1' and out['sym'] == 16
    assert out['pad_metadata']['sound.playmode'] == 'oneshot'
    assert out['slot_metadata']['crc'] == 2727906176
    sent = [call.args[1] for call in d.request.call_args_list]
    assert sent[0][2:4] == (3207).to_bytes(2, 'big') and sent[1][2:4] == (16).to_bytes(2, 'big')


def test_read_pad_unassigned_reads_no_slot():
    d = DeviceSession()
    d.greet = Mock()
    d.begin_read = Mock()
    d.request = Mock(side_effect=_pages(b'{"sym":0}'))
    out = d.read_pad(2, 'D', 12)
    assert out['sym'] == 0 and out['slot_metadata'] is None
    assert d.request.call_count == 1


def test_read_pad_rejects_bad_destination_before_io():
    d = DeviceSession()
    d.request = Mock()
    with pytest.raises(ValueError):
        d.read_pad(1, 'E', 1)
    d.request.assert_not_called()


def test_pad_metadata_requires_sym():
    d = DeviceSession()
    d.request = Mock(side_effect=_pages(b'{"name":"not a pad"}'))
    with pytest.raises(DeviceRejected):
        d.pad_metadata(1, 'A', 1)
