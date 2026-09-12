"""delete_samples against a fake device.

Deleting is the one destructive operation with no undo, so these tests are mostly about what it
refuses: a slot a project still stores, a slot that is not there, a plan the owner has not
approved, and a device that answers ok without deleting anything.
"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ep133_mcp.device import DeviceError, DeviceRejected
from ep133_mcp.safety.delete import Deleter, referencing_pads
from ep133_mcp.safety.errors import InvalidDestination
from ep133_mcp.safety.journal import Journal
from test_install import FakeDevice


class DeletingDevice(FakeDevice):
    def __init__(self):
        super().__init__()
        self.slots = {11, 15, 16, 18}
        self.samples = {slot: {'crc': slot, 'sample.end': 100 * slot, 'name': f'{slot:03d} sound.wav'}
                        for slot in self.slots}
        self.deletes = []
        self.ignore = set()          # a device that answers ok and changes nothing
        self.fail = None

    def delete_slot(self, slot):
        self.deletes.append(slot)
        if slot == self.fail:
            raise DeviceRejected('device rejected the delete', slot=slot, status=1, reason='failed to delete')
        if slot in self.ignore:
            return False
        self.slots.discard(slot)
        self.samples.pop(slot, None)
        self.free += 1000
        return True


@pytest.fixture
def setup(tmp_path):
    from ep133_mcp.safety.backup import snapshot
    d = DeletingDevice()
    backups = SimpleNamespace(require_current=Mock(side_effect=lambda *_: snapshot(d)), invalidate=Mock())
    return d, Deleter(backups, Journal(tmp_path / 'journal'))


def approve(deleter, device, slots):
    first = deleter.delete(slots, 'backup', device)
    assert first['status'] == 'needs_confirmation'
    return deleter.delete(slots, 'backup', device, confirm=first['confirm'])


def test_confirmation_names_what_goes_and_then_deletes(setup):
    d, deleter = setup
    first = deleter.delete([15, 16], 'backup', d)
    assert first['status'] == 'needs_confirmation' and d.deletes == []
    assert [e['slot'] for e in first['impact']] == [15, 16]
    assert first['impact'][0]['name'] == '015 sound.wav' and first['impact'][0]['frames'] == 1500
    assert 'restore' in first['undo']

    result = deleter.delete([15, 16], 'backup', d, confirm=first['confirm'])
    assert result['status'] == 'deleted' and d.deletes == [15, 16]
    assert [e['status'] for e in result['entries']] == ['deleted', 'deleted']
    assert result['library_slots_before'] == 4 and result['library_slots_after'] == 2
    assert d.slots == {11, 18}
    assert Journal(deleter.journal.directory).latest('x' * 64, 'delete_samples') is None
    saved = deleter.journal.latest(_device_id(d), 'delete_samples')
    assert saved['id'] == result['journal_id'] and saved['status'] == 'deleted'


def _device_id(device):
    from ep133_mcp.safety.backup import snapshot
    from ep133_mcp.safety.install import device_id
    return device_id(snapshot(device))


def test_refuses_a_slot_a_project_still_stores(setup):
    d, deleter = setup
    d.pads[3, 'D', 9] = (16, 1600)
    with pytest.raises(InvalidDestination) as e:
        deleter.delete([15, 16], 'backup', d)
    assert '16' in str(e.value.detail['observed'])
    assert d.deletes == []
    # The pad that holds it is named, so the owner knows what to clear.
    assert e.value.detail['observed']['16'] == [{'project': 3, 'group': 'D', 'pad': 9,
                                                 'stored_length': 1600}]
    # Clearing the pad makes the same call legal.
    d.pads[3, 'D', 9] = (0, 0)
    assert approve(deleter, d, [16])['status'] == 'deleted'


def test_refuses_absent_slots_and_bad_input(setup):
    d, deleter = setup
    for bad in ([], [0], [1000], [15, 15], 'abc', list(range(1, 200))):
        with pytest.raises(InvalidDestination):
            deleter.delete(bad, 'backup', d)
    with pytest.raises(InvalidDestination):
        deleter.delete([77], 'backup', d)               # not in the library
    assert d.deletes == []


def test_a_device_that_ignores_the_command_is_not_success(setup):
    """FILE_DELETE is unverified: the result must come from re-reading the slot."""
    d, deleter = setup
    d.ignore = {15}
    result = approve(deleter, d, [15, 16])
    assert [e['status'] for e in result['entries']] == ['not_deleted', 'deleted']
    assert result['status'] == 'deleted'                # every entry was attempted
    assert 15 in d.slots and 16 not in d.slots


def test_a_failure_stops_and_is_reported(setup):
    d, deleter = setup
    d.fail = 16
    result = approve(deleter, d, [15, 16, 18])
    assert result['status'] == 'partial'
    assert [e['status'] for e in result['entries']] == ['deleted', 'failed']
    assert result['entries'][1]['failure']['reason'] == 'failed to delete'
    assert result['entries'][1]['failure']['status'] == 1                 # the device's own status/reason survive
    assert 18 in d.slots and d.deletes == [15, 16]      # stopped before the third


def test_confirmation_is_bound_to_the_exact_slots(setup):
    d, deleter = setup
    first = deleter.delete([15], 'backup', d)
    again = deleter.delete([15, 16], 'backup', d, confirm=first['confirm'])
    assert again['status'] == 'needs_confirmation'       # a different plan needs its own approval
    assert d.deletes == []


def test_delete_journal_does_not_shadow_the_install_undo(setup, tmp_path):
    """Both operations share the journal directory; undo_last_install must still find the install."""
    from ep133_mcp.safety.install import Installer
    from test_preflight import wav_file

    d, deleter = setup
    installer = Installer(deleter.backups, deleter.journal)
    installer.install([{'pad': 1, 'path': str(wav_file(tmp_path))}], 1, 'A', 'backup', d)
    approve(deleter, d, [15])
    identity = _device_id(d)
    assert deleter.journal.latest(identity, 'delete_samples')['operation'] == 'delete_samples'
    assert deleter.journal.latest(identity)['operation'] == 'install_kit'
    assert installer.undo(d)['status'] in ('undone', 'partial')


def test_referencing_pads_lists_every_project(setup):
    d, _ = setup
    d.pads[1, 'A', 1] = (11, 10)
    d.pads[7, 'C', 12] = (11, 10)
    from ep133_mcp.safety.backup import snapshot
    refs = referencing_pads(snapshot(d))
    assert sorted(r['project'] for r in refs[11]) == [1, 7]
    assert 15 not in refs
