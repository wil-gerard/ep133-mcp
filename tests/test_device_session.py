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


def test_send_note_sends_on_then_off_on_the_port():
    d = DeviceSession()
    d._out = Mock()
    out = d.send_note(2, 60, 90, 0.01)
    sent = [call.args[0] for call in d._out.send.call_args_list]
    assert [(m.type, m.channel, m.note, m.velocity) for m in sent] == [('note_on', 1, 60, 90), ('note_off', 1, 60, 0)]
    assert out['channel'] == 2 and out['held_s'] >= 0.01
    for bad in ((0, 60), (17, 60), (1, 128), (1, 60, 0), (1, 60, 100, 0.0), (1, 60, 100, 9)):
        with pytest.raises(ValueError):
            d.send_note(*bad)
    d._out = None
    with pytest.raises(DeviceUnavailable):
        d.send_note(1, 60)


def _entry(node, flags, size, name):
    return node.to_bytes(2, 'big') + bytes([flags]) + size.to_bytes(4, 'big') + name.encode() + b'\0'


def test_list_directory_pages_until_empty():
    d = DeviceSession()
    pages = [Response(0, 0, 5, 0, b'\0\0' + _entry(1000, 0, 0, 'sounds') + _entry(2000, 0, 0, 'projects')),
             Response(0, 0, 5, 0, b'\0\1' + _entry(5000, 1, 224, 'settings')),
             Response(0, 0, 5, 0, b'\0\2')]
    d.request = Mock(side_effect=pages)
    out = d.list_directory(0)
    assert [(e['node'], e['kind'], e['size'], e['name']) for e in out] == [
        (1000, 'folder', 0, 'sounds'), (2000, 'folder', 0, 'projects'), (5000, 'file', 224, 'settings')]
    sent = [call.args[1] for call in d.request.call_args_list]
    assert sent == [bytes.fromhex('0400000000'), bytes.fromhex('0400010000'), bytes.fromhex('0400020000')]
    d.request = Mock(return_value=Response(0, 0, 5, 1, b'invalid id\0'))
    with pytest.raises(DeviceRejected):
        d.list_directory(7)
    d.request = Mock(return_value=Response(0, 0, 5, 0, b'\0\5' + _entry(1, 1, 1, 'x')))
    with pytest.raises(DeviceRejected):                    # page index mismatch
        d.list_directory(0)


def test_walk_descends_folders_and_skips():
    d = DeviceSession()
    tree = {0: [_entry(1000, 0, 0, 'sounds'), _entry(2000, 0, 0, 'projects'), _entry(9, 1, 10, 'top')],
            1000: [_entry(1, 1, 5, '001.pcm')],
            2000: [_entry(3000, 0, 0, 'P01')],
            3000: [_entry(3100, 0, 0, 'groups')],
            3100: []}

    def request(command, payload):
        node = int.from_bytes(payload[3:5], 'big')
        page = int.from_bytes(payload[1:3], 'big')
        body = b''.join(tree[node]) if page == 0 else b''
        return Response(0, 0, 5, 0, page.to_bytes(2, 'big') + body)
    d.request = Mock(side_effect=request)
    out = d.walk(0, max_depth=3, skip={1000})
    assert [e['path'] for e in out] == ['/sounds', '/projects', '/top', '/projects/P01', '/projects/P01/groups']
    assert out[3]['parent'] == 2000 and out[3]['depth'] == 1
    assert [e['path'] for e in d.walk(0, max_depth=1)] == ['/sounds', '/projects', '/top']
    assert '/sounds/001.pcm' in [e['path'] for e in d.walk(0, max_depth=2)]
    with pytest.raises(DeviceRejected):
        d.walk(0, max_nodes=2)


def test_stat_parses_and_reports_absence():
    d = DeviceSession()
    d.request = Mock(return_value=Response(0, 0, 5, 0, (5000).to_bytes(2, 'big') + (3000).to_bytes(2, 'big')
                                           + b'\1' + (224).to_bytes(4, 'big') + b'settings\0'))
    assert d.stat(5000) == {'node': 5000, 'parent': 3000, 'flags': 1, 'size': 224, 'name': 'settings', 'kind': 'file'}
    assert d.request.call_args.args[1] == bytes.fromhex('0b1388')
    d.request = Mock(return_value=Response(0, 0, 5, 1, b'invalid id\0'))
    assert d.stat(1234) is None
    d.request = Mock(return_value=Response(0, 0, 5, 1, b'not initialized\0'))
    with pytest.raises(DeviceRejected):
        d.stat(1234)
    d.request = Mock(return_value=Response(0, 0, 5, 0, b'\1\2'))
    with pytest.raises(DeviceRejected):
        d.stat(1234)
