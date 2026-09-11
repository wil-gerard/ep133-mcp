"""Read-only hardware acceptance check for the latest installed journal.

Stop the MCP server and close Sample Tool first. The owner must be present.
Run after the owner power-cycles the device to collect persistence evidence:

    uv run python tools/verify_installed_journal.py

This script cannot establish that a power cycle occurred or confirm audible
playback. Record those owner observations separately. Output contains no serial
or audio. DeviceSession holds the same process lock as the server.
"""

import argparse
import json
import os
from pathlib import Path

from ep133_mcp.device import DeviceSession
from ep133_mcp.safety.install import device_id, pad_record
from ep133_mcp.safety.journal import Journal


def verify(device, journal):
    greeting = device.greet()
    record = journal.latest(device_id({'sku': greeting.sku, 'serial': greeting.serial}))
    if record is None:
        return {'status': 'failed', 'reason': 'No journal for this device.'}
    eligible = [e for e in record['entries'] if e['status'] in ('installed', 'assignment_attempted')]
    if not eligible:
        return {'status': 'failed', 'journal_id': record['id'],
                'reason': 'No installed or uncertain assignments to verify.'}
    entries = []
    for entry in eligible:
        device.begin_read()
        meta = device.metadata(entry['slot']) or {}
        stored = pad_record(device, entry['project'], entry['group'], entry['pad'])
        matched = (meta.get('crc') == entry['crc'] and meta.get('sample.end') == entry['frames']
                   and stored == (entry['slot'], entry['frames']))
        entries.append({'project': entry['project'], 'group': entry['group'], 'pad': entry['pad'],
                        'slot': entry['slot'], 'expected_crc': entry['crc'],
                        'actual_crc': meta.get('crc'), 'expected_frames': entry['frames'],
                        'actual_frames': meta.get('sample.end'), 'stored_slot': stored[0],
                        'stored_length': stored[1], 'matched': matched})
    return {'status': 'matched' if all(e['matched'] for e in entries) else 'failed',
            'journal_id': record['id'], 'sku': greeting.sku, 'os_version': greeting.os_version,
            'entries': entries, 'power_cycle_and_playback': 'Require separate owner observation.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--journal-dir', default=os.environ.get(
        'EP133_JOURNAL_DIR', str(Path.home() / '.local/state/ep133-mcp/journal')))
    args = parser.parse_args()
    with DeviceSession() as device:
        result = verify(device, Journal(args.journal_dir))
    print(json.dumps(result, indent=2))
    return 0 if result['status'] == 'matched' else 1


if __name__ == '__main__':
    raise SystemExit(main())
