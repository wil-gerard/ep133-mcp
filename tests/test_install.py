import copy
import io
import struct
import tarfile
from types import SimpleNamespace
from unittest.mock import Mock
import zlib

import pytest

from ep133_mcp.device import DeviceError
from ep133_mcp.safety.install import Installer
from ep133_mcp.safety.journal import Journal
from ep133_mcp.safety.errors import JournalError, TooLarge, UnsupportedFormat
from test_preflight import wav_file


class FakeDevice:
    def __init__(self):
        self.slots = {16}
        self.pads = {(p, g, n): (0, 0) for p in range(1, 10) for g in 'ABCD' for n in range(1, 13)}
        self.samples = {}
        self.writes = []
        self.bad_crc = False
        self.fail_upload = None
        self.fail_assignment = False
        self.keep_length_on_clear = False
        self.free = 100000

    def greet(self):
        return SimpleNamespace(sku='TE032AS001', os_version='2.5.1', serial='test', product='EP-133')

    def begin_read(self):
        return None

    def slot_exists(self, slot):
        return slot in self.slots

    def sample_root(self):
        return SimpleNamespace(free_space_in_bytes=self.free)

    def project_tar(self, project):
        out = io.BytesIO()
        with tarfile.open(fileobj=out, mode='w') as t:
            for (p, g, n), (slot, length) in self.pads.items():
                if p != project:
                    continue
                raw = bytearray(26)
                struct.pack_into('<H', raw, 1, slot)
                struct.pack_into('<I', raw, 8, length)
                m = tarfile.TarInfo(f'pads/{g.lower()}/p{n:02}')
                m.size = 26
                t.addfile(m, io.BytesIO(raw))
        return out.getvalue()

    def upload_sample(self, slot, name, pcm):
        self.writes.append(('upload', slot))
        if slot == self.fail_upload:
            raise DeviceError('upload interrupted')
        self.slots.add(slot)
        self.samples[slot] = {'crc': zlib.crc32(pcm), 'sample.end': len(pcm) // 2}
        self.free -= len(pcm)

    def metadata(self, slot):
        if slot == 0:
            return {}
        return self.samples[slot] | ({'crc': -1} if self.bad_crc else {})

    def pad_metadata(self, project, group, pad):
        return {'sym': self.pads[project, group, pad][0]}

    def assign_pad(self, node, slot):
        self.writes.append(('assign', node, slot))
        project = (node - 2000) // 1000
        rest = node - 2000 - project * 1000 - 200
        group, pad = 'ABCD'[rest // 100], rest % 100
        length = self.samples[slot]['sample.end'] if slot else (
            self.pads[project, group, pad][1] if self.keep_length_on_clear else 0)
        self.pads[project, group, pad] = (slot, length)
        if self.fail_assignment:
            raise DeviceError('assignment ACK lost')


@pytest.fixture
def setup(tmp_path):
    from ep133_mcp.safety.backup import snapshot
    d = FakeDevice()
    backups = SimpleNamespace(require_current=Mock(side_effect=lambda *_: snapshot(d)), invalidate=Mock())
    installer = Installer(backups, Journal(tmp_path / 'journal'))
    mapping = [{'pad': 1, 'path': str(wav_file(tmp_path))}]
    return d, installer, mapping


def test_install_and_durable_undo(setup):
    d, installer, mapping = setup
    result = installer.install(mapping, 1, 'A', 'backup', d)
    assert result['status'] == 'installed'
    assert result['power_cycle_verified'] is False
    assert d.pads[1, 'A', 1] == (1, 10)
    # A new orchestrator can undo the durable journal.
    other = Installer(installer.backups, Journal(installer.journal.directory))
    undone = other.undo(d)
    assert undone['status'] == 'undone'
    assert undone['library_slots_left_in_place'] == [1]
    assert d.pads[1, 'A', 1] == (0, 0)
    assert 1 in d.slots


def test_crc_failure_never_assigns(setup):
    d, installer, mapping = setup
    d.bad_crc = True
    result = installer.install(mapping, 1, 'A', 'backup', d)
    assert result['status'] == 'partial'
    assert result['entries'][0]['status'] == 'upload_attempted'
    assert d.writes == [('upload', 1)]


def test_lost_assignment_ack_can_be_undone(setup):
    d, installer, mapping = setup
    d.fail_assignment = True
    result = installer.install(mapping, 1, 'A', 'backup', d)
    assert result['entries'][0]['status'] == 'assignment_attempted'
    d.fail_assignment = False
    assert installer.undo(d)['status'] == 'undone'


def test_confirmation_binds_pcm_and_target(setup, tmp_path):
    d, installer, mapping = setup
    d.pads[1, 'A', 1] = (16, 30)
    result = installer.install(mapping, 1, 'A', 'backup', d)
    assert result['status'] == 'needs_confirmation' and d.writes == []
    assert result['impact'][0]['prior_slot'] == 16
    token = result['confirm']
    wav_file(tmp_path, frames=11)
    replacement = installer.install(mapping, 1, 'A', 'backup', d, token)
    assert replacement['status'] == 'needs_confirmation' and d.writes == []
    accepted = installer.install(mapping, 1, 'A', 'backup', d, replacement['confirm'])
    assert accepted['status'] == 'installed'


def test_kit_preflights_all_and_stops_at_failure(setup, tmp_path):
    d, installer, mapping = setup
    mapping += [{'pad': 2, 'path': '/missing.wav'}]
    with pytest.raises(UnsupportedFormat):
        installer.install(mapping, 1, 'A', 'backup', d)
    assert d.writes == []
    mapping[1]['path'] = mapping[0]['path']
    mapping += [{'pad': 3, 'path': mapping[0]['path']}]
    d.fail_upload = 2
    result = installer.install(mapping, 1, 'A', 'backup', d)
    assert [e['status'] for e in result['entries']] == ['installed', 'upload_attempted', 'pending']
    assert result['transactional'] is False
    undo = installer.undo(d)
    assert undo['status'] == 'undo_partial'
    assert d.pads[1, 'A', 1] == (0, 0)


def test_changed_pad_is_not_overwritten_by_undo(setup):
    d, installer, mapping = setup
    installer.install(mapping, 1, 'A', 'backup', d)
    d.pads[1, 'A', 1] = (16, 30)
    before = list(d.writes)
    result = installer.undo(d)
    assert result['status'] == 'undo_partial'
    assert d.writes == before


def test_stale_prior_reference_is_cleared_not_rewritten(setup):
    d, installer, mapping = setup
    d.pads[1, 'A', 1] = (30, 0)                       # points at a slot the library does not have
    installer.install(mapping, 1, 'A', 'backup', d)
    out = installer.undo(d)
    assert out['status'] == 'undone'
    entry = next(e for e in out['entries'] if e['pad'] == 1)
    assert entry['status'] == 'undone' and 'prior slot 30 is absent' in entry['undo_note']
    assert d.pads[1, 'A', 1] == (0, 0)                # cleared, never re-pointed at 30


def test_undo_accepts_a_cleared_pad_whose_length_the_device_kept(setup):
    d, installer, mapping = setup
    installer.install(mapping, 1, 'A', 'backup', d)
    d.keep_length_on_clear = True                     # as observed after a power-cycle (P1 B09)
    out = installer.undo(d)
    assert out['status'] == 'undone'
    assert all(e['status'] == 'undone' for e in out['entries'])
    assert d.pads[1, 'A', 1][0] == 0 and d.pads[1, 'A', 1][1] > 0
    assert out['entries'][0]['undo_note'] == (
        f"pad cleared (JSON sym 0); the device left stored length {d.pads[1, 'A', 1][1]} in the project record")


def test_journal_failure_prevents_writes(setup, monkeypatch):
    d, installer, mapping = setup
    def fail(*args):
        raise JournalError('disk full', next_step='Free space')
    monkeypatch.setattr(installer.journal, 'save', fail)
    with pytest.raises(JournalError):
        installer.install(mapping, 1, 'A', 'backup', d)
    assert d.writes == []


def test_memory_and_state_rechecked_before_writes(setup, monkeypatch):
    d, installer, mapping = setup
    original = installer.journal.create
    def mutate(*args):
        result = original(*args)
        d.pads[9, 'D', 12] = (1, 0)
        return result
    monkeypatch.setattr(installer.journal, 'create', mutate)
    result = installer.install(mapping, 1, 'A', 'backup', d)
    assert result['status'] == 'partial' and d.writes == []


def test_change_during_upload_stops_assignment(setup, monkeypatch):
    d, installer, mapping = setup
    original = d.upload_sample
    def mutate(*args):
        original(*args)
        d.pads[1, 'A', 1] = (16, 30)
    monkeypatch.setattr(d, 'upload_sample', mutate)
    result = installer.install(mapping, 1, 'A', 'backup', d)
    assert result['entries'][0]['status'] == 'upload_attempted'
    assert d.writes == [('upload', 1)]


def test_expired_confirmation_reissues_without_writing(setup):
    d, installer, mapping = setup
    d.pads[1, 'A', 1] = (16, 30)
    first = installer.install(mapping, 1, 'A', 'backup', d)
    binding, _ = installer._confirmations[first['confirm']]
    installer._confirmations[first['confirm']] = (binding, 0)
    second = installer.install(mapping, 1, 'A', 'backup', d, first['confirm'])
    assert second['status'] == 'needs_confirmation'
    assert second['confirm'] != first['confirm'] and d.writes == []


def test_read_only_acceptance_helper_checks_journal(setup):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('verify_installed_journal',
        Path(__file__).parents[1] / 'tools/verify_installed_journal.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    d, installer, mapping = setup
    assert helper.verify(d, installer.journal)['status'] == 'failed'
    installer.install(mapping, 1, 'A', 'backup', d)
    before = list(d.writes)
    result = helper.verify(d, installer.journal)
    assert result['status'] == 'matched' and 'serial' not in result
    assert d.writes == before
    d.bad_crc = True
    assert helper.verify(d, installer.journal)['status'] == 'failed'
