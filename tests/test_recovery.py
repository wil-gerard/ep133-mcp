import hashlib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ep133_mcp.protocol.projects import stored_pads, unpack_project, pack_project
from ep133_mcp.safety.backup import snapshot
from ep133_mcp.safety.capture import wav_bytes
from ep133_mcp.safety.errors import InvalidDestination, VerificationFailed
from ep133_mcp.safety.import_project import Importer
from ep133_mcp.safety.journal import Journal
from ep133_mcp.safety.recovery import parse_sound, plan_sounds, Restorer
from test_import_project import WriteDevice, ppak, wav, minimal_project, blank_ppak, approve, write_backup_pak


class Device(WriteDevice):
    def __init__(self):
        super().__init__()
        self.audio = {}

    def upload_sample(self, slot, name, pcm):
        super().upload_sample(slot, name, pcm)
        self.audio[slot] = pcm
        self.samples[slot]['name'] = name

    def set_metadata(self, slot, fields):
        self.samples[slot].update(fields)

    def slot_pcm(self, slot):
        return self.audio[slot]


def setup(tmp_path):
    d = Device()
    backups = SimpleNamespace(require_current=Mock(side_effect=lambda *_: snapshot(d)), invalidate=Mock(),
                              path_of=Mock(return_value=str(tmp_path / 'session.pak')))
    return d, backups, Journal(tmp_path / 'journal')


def test_import_uploads_then_remaps_and_undo_keeps_audio(tmp_path):
    d, b, j = setup(tmp_path)
    write_backup_pak(d, tmp_path / 'session.pak')
    bid = hashlib.sha256((tmp_path / 'session.pak').read_bytes()).hexdigest()
    path = ppak(tmp_path, sounds={'/sounds/016 tone.wav': wav()})
    importer = Importer(b, j)
    first = importer.import_ppak(path, 7, bid, d, slot_map={'16': 40})
    assert first['impact']['sounds'][0]['slot'] == 40
    result = importer.import_ppak(path, 7, bid, d, first['confirm'], {'16': 40})
    assert result['status'] == 'imported'
    assert result['sounds'][0]['status'] == 'restored'
    assert stored_pads(d.tars[7])[6]['stored_slot'] == 40
    assert importer.undo(d)['status'] == 'undone'
    assert 40 in d.slots


def test_restore_metadata_and_reuse_requires_exact_audio(tmp_path):
    d, b, j = setup(tmp_path)
    data = wav_bytes(bytes(200), {'name': 'tone', 'sound.pan': 2, 'sound.rootnote': 60})
    source = ppak(tmp_path, sounds={'/sounds/040 tone.wav': data})
    restorer = Restorer(b, j)
    first = restorer.restore(source, [40], 'backup', d)
    result = restorer.restore(source, [40], 'backup', d, first['confirm'])
    assert result['status'] == 'restored'
    assert d.samples[40]['sound.pan'] == 2
    assert plan_sounds({'/sounds/040 tone.wav': data}, d)[0]['action'] == 'reuse'
    d.audio[40] = b'corrupt'
    with pytest.raises(InvalidDestination):
        plan_sounds({'/sounds/040 tone.wav': data}, d)


def test_upload_failure_never_writes_project(tmp_path):
    d, b, j = setup(tmp_path)
    d.fail_upload = 40
    path = ppak(tmp_path, files=minimal_project(pad7_slot=40), sounds={'/sounds/040 tone.wav': wav()})
    imp = Importer(b, j)
    first = imp.import_ppak(path, 7, 'backup', d)
    out = imp.import_ppak(path, 7, 'backup', d, first['confirm'])
    assert out['status'] == 'partial' and not d.written
    assert out['sounds'][0]['status'] == 'upload_attempted'


def test_raw_settings_mismatch_fails_and_undo_refuses_drift(tmp_path):
    d, b, j = setup(tmp_path)
    write_backup_pak(d, tmp_path / 'session.pak')
    imp = Importer(b, j)
    def corrupt(tar):
        files = unpack_project(tar)
        files['settings'] = files['settings'][:-1] + b'\x01'
        return pack_project(files)
    d.rewrite = corrupt
    _, out = approve(imp, d, blank_ppak(tmp_path), 7)
    assert out['status'] == 'partial' and 'settings' in out['differences']
    d.rewrite = None
    d.written.clear()
    _, out = approve(imp, d, blank_ppak(tmp_path, bpm=110, name='next.ppak'), 7)
    d.tars[7] = corrupt(d.tars[7])
    with pytest.raises(VerificationFailed):
        imp.undo(d)


def test_truncated_audio_mapping_and_capacity_refused(tmp_path):
    d, _, _ = setup(tmp_path)
    with pytest.raises(InvalidDestination):
        parse_sound('tone', wav()[:-2])
    sounds = {'/sounds/040 tone.wav': wav()}
    with pytest.raises(InvalidDestination):
        plan_sounds(sounds, d, {'40': 0})
    d.free = 200
    with pytest.raises(InvalidDestination):
        plan_sounds(sounds, d)
