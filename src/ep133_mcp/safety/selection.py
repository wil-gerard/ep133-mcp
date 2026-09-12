"""Explicit, confirmed active-project selection with a durable previous value."""

import hashlib
import json
import secrets
import time

from .errors import InvalidDestination, VerificationFailed
from .install import device_id


class ProjectSelector:
    def __init__(self, backups, journal):
        self.backups, self.journal = backups, journal
        self.confirmations = {}

    def select(self, project, backup_id, device, confirm=None):
        if type(project) is not int or not 1 <= project <= 9:
            raise InvalidDestination('project must be 1..9', next_step='Choose an existing project.')
        live = self.backups.require_current(backup_id, device)
        device.begin_read()
        before = device.active_project()
        if before == project:
            return {'status': 'unchanged', 'active_project': project}
        impact = {'previous_project': before, 'active_project': project}
        binding = hashlib.sha256(json.dumps([device_id(live), backup_id, impact], sort_keys=True).encode()).hexdigest()
        previous = self.confirmations.pop(confirm, None)
        if previous is None or previous[0] != binding or previous[1] < time.monotonic():
            self.confirmations = {k: v for k, v in self.confirmations.items() if v[1] >= time.monotonic()}
            token = secrets.token_urlsafe(32)
            self.confirmations[token] = (binding, time.monotonic() + 300)
            return {'status': 'needs_confirmation', 'impact': impact, 'confirm': token,
                    'instruction': 'Approve this project switch before repeating.'}
        record = self.journal.create(device_id(live), backup_id, [impact | {'status': 'write_attempted'}],
                                     operation='set_active_project')
        self.backups.invalidate()
        try:
            device.set_metadata(2000, {'active': 2000 + 1000 * project})
            device.begin_read()
            actual = device.active_project()
            if actual != project:
                raise VerificationFailed('Active project read-back differs', observed=actual, expected=project,
                                         next_step='Inspect the device before proceeding.')
            record['status'] = record['entries'][0]['status'] = 'selected'
        except Exception as e:
            record['status'] = 'partial'
            record['failure'] = {'error': type(e).__name__, 'message': str(e)}
            self.journal.save(record)
            raise
        self.journal.save(record)
        return {'status': 'selected', **impact, 'journal_id': record['id'], 'power_cycle_verified': False}
