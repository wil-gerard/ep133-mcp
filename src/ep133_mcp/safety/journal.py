"""Private durable write-ahead records. Never store PCM or backup contents."""

import json
import os
from pathlib import Path
import tempfile
import uuid

from .errors import JournalError


class Journal:
    def __init__(self, directory):
        self.directory = Path(directory).expanduser()

    def save(self, record):
        temporary = None
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode='w', dir=self.directory, delete=False) as f:
                temporary = f.name
                json.dump(record, f, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, self.directory / (record['id'] + '.json'))
            temporary = None
            fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except (OSError, ValueError) as e:
            raise JournalError('Could not persist install journal', observed=str(e),
                               next_step='Check journal directory permissions and disk space before retrying.') from e
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

    def create(self, device_id, backup_id, entries, operation='install_kit'):
        import time
        record = {'id': uuid.uuid4().hex, 'created_at': time.time(),
                  'device_id': device_id, 'backup_id': backup_id, 'status': 'in_progress',
                  'operation': operation, 'entries': entries}
        self.save(record)
        return record

    def latest(self, device_id, operation='install_kit'):
        """The newest record of one operation for this device.

        Operations share the journal directory, so a delete written after an install must not
        shadow it: undo_last_install reverts pad writes and has nothing to say about a deleted
        slot. Records written before operations were tagged are installs."""
        records = []
        try:
            for path in self.directory.glob('*.json'):
                record = json.loads(path.read_text())
                if record['device_id'] == device_id and record.get('operation', 'install_kit') == operation:
                    records.append(record)
            return max(records, key=lambda r: r['created_at'], default=None)
        except (OSError, ValueError, KeyError, TypeError) as e:
            raise JournalError('Could not read install journal', observed=str(e),
                               next_step='Inspect the private journal directory before undoing changes.') from e
