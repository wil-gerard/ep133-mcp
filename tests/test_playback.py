import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ep133_mcp.midi.playback import Playback, compile_pattern
from ep133_mcp.safety.selection import ProjectSelector
from ep133_mcp.safety.journal import Journal
from ep133_mcp.safety.errors import VerificationFailed


def pattern():
    return [{'group': 'A', 'index': 1, 'bars': 1, 'steps': {'1': 'x...o...........'}}]


def routing():
    return [{'group': 'A', 'pad': 1, 'channel': 1, 'note': 36}]


def test_compile_preserves_simultaneous_notes_and_soft_hits():
    patterns = pattern()
    patterns[0]['steps']['2'] = 'x...............'
    routes = routing() + [{'group': 'A', 'pad': 2, 'channel': 1, 'note': 37}]
    events, duration, pads = compile_pattern(patterns, routes, 120)
    assert [(e[2], e[3]) for e in events if e[0] == 0] == [(1, 36), (1, 37)]
    assert any(e[-1] == 60 for e in events)
    assert duration == 2 and pads == [('A', 1), ('A', 2)]


@pytest.mark.parametrize('bpm', [True, float('nan'), 0, 301])
def test_invalid_timing_refused(bpm):
    with pytest.raises(ValueError):
        compile_pattern(pattern(), routing(), bpm)


def test_stop_releases_notes_and_allows_another_audition():
    player = Playback()
    sent = []
    heard = threading.Event()
    def send(channel, note, velocity, on):
        sent.append((channel, note, velocity, on))
        if on:
            heard.set()
    device = SimpleNamespace(send_midi_note=send)
    lock = threading.RLock()
    player.start(device, lock, [(0, 1, 1, 36, 100), (10, 0, 1, 36, 0)], 10, [])
    assert heard.wait(1)
    assert player.stop()['status'] == 'stopped'
    assert sent[-1] == (1, 36, 0, False)
    player.start(device, lock, [(0, 1, 1, 36, 100), (.01, 0, 1, 36, 0)], .02, [])
    player._thread.join(1)
    assert player.status()['status'] == 'finished'


def test_send_failure_still_attempts_note_off():
    player = Playback()
    sent = []
    def send(channel, note, velocity, on):
        sent.append(on)
        if on:
            raise OSError('port gone')
    player.start(SimpleNamespace(send_midi_note=send), threading.RLock(), [(0, 1, 1, 36, 100)], .01, [])
    player._thread.join(1)
    assert player.status()['status'] == 'failed' and sent == [True, False]


def test_stop_queued_playback_never_sends():
    player = Playback()
    d = SimpleNamespace(send_midi_note=Mock())
    lock = threading.Lock()
    with lock:
        player.start(d, lock, [(0, 1, 1, 36, 100)], 1, [])
        player._cancel.set()
    player._thread.join(1)
    assert player.status()['status'] == 'stopped'
    d.send_midi_note.assert_not_called()


def test_project_selection_confirms_journals_and_reads_back(tmp_path):
    live = {'sku': 'TE032AS001', 'serial': 'test'}
    b = SimpleNamespace(require_current=Mock(return_value=live), invalidate=Mock())
    d = SimpleNamespace(begin_read=Mock(), active_project=Mock(return_value=3), set_metadata=Mock())
    def write(node, fields):
        assert node == 2000 and fields == {'active': 9000}
        d.active_project.return_value = 7
    d.set_metadata.side_effect = write
    selector = ProjectSelector(b, Journal(tmp_path))
    first = selector.select(7, 'backup', d)
    d.set_metadata.assert_not_called()
    out = selector.select(7, 'backup', d, first['confirm'])
    assert out['status'] == 'selected' and out['previous_project'] == 3
    assert selector.select(7, 'backup', d)['status'] == 'unchanged'


def test_selection_ack_without_change_is_failure(tmp_path):
    b = SimpleNamespace(require_current=Mock(return_value={'sku': 's', 'serial': 'x'}), invalidate=Mock())
    d = SimpleNamespace(begin_read=Mock(), active_project=Mock(return_value=3), set_metadata=Mock())
    selector = ProjectSelector(b, Journal(tmp_path))
    first = selector.select(7, 'backup', d)
    with pytest.raises(VerificationFailed):
        selector.select(7, 'backup', d, first['confirm'])
