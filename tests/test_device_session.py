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
