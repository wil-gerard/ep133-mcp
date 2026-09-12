"""Bounded live audition with explicit MIDI routing and cancellable note cleanup."""

import math
import threading
import time
import uuid

from ..protocol.patterns import encode_pattern
from ..protocol.payloads import pad_node


def compile_pattern(patterns, routing, bpm, repeats=1):
    if isinstance(bpm, bool) or not isinstance(bpm, (int, float)) or not math.isfinite(bpm) or not 40 <= bpm <= 300:
        raise ValueError('bpm must be 40..300')
    if type(repeats) is not int or not 1 <= repeats <= 8:
        raise ValueError('repeats must be 1..8')
    routes = {}
    for route in routing:
        if set(route) != {'group', 'pad', 'channel', 'note'}:
            raise ValueError('routing entries require group, pad, channel and note')
        group, pad, channel, note = (route[k] for k in ('group', 'pad', 'channel', 'note'))
        pad_node(1, group, pad)
        if type(channel) is not int or not 1 <= channel <= 16 or type(note) is not int or not 0 <= note <= 127:
            raise ValueError('channel must be 1..16 and note 0..127')
        if (group, pad) in routes or (channel, note) in routes.values():
            raise ValueError('Duplicate pad or MIDI routing')
        routes[group, pad] = (channel, note)
    if not patterns or len(patterns) > 4:
        raise ValueError('Provide one pattern per group, at most four')
    rows = []
    groups = set()
    bars = None
    for pattern in patterns:
        if set(pattern) != {'group', 'index', 'bars', 'steps'}:
            raise ValueError('Use generate_ppak steps patterns: group, index, bars, steps')
        group = pattern['group']
        if group not in ('A', 'B', 'C', 'D') or group in groups:
            raise ValueError('Provide each group once')
        groups.add(group)
        if type(pattern['index']) is not int or not 1 <= pattern['index'] <= 99:
            raise ValueError('index must be 1..99')
        if bars is not None and pattern['bars'] != bars:
            raise ValueError('Audition patterns must have equal bar counts')
        bars = pattern['bars']
        steps = {int(k): v for k, v in pattern['steps'].items()}
        if len(steps) != len(pattern['steps']):
            raise ValueError('Duplicate pad')
        encode_pattern(bars, steps)
        for pad, row in steps.items():
            if (group, pad) not in routes:
                raise ValueError(f'Missing explicit MIDI route for {group}{pad}')
            rows.append((group, pad, row))
    step_s = 60 / bpm / 4
    duration = bars * 16 * step_s * repeats
    if duration > 120:
        raise ValueError('Audition is limited to 120 seconds')
    events = []
    for repeat in range(repeats):
        for group, pad, row in rows:
            channel, note = routes[group, pad]
            for step, hit in enumerate(row):
                if hit == '.':
                    continue
                at = (repeat * bars * 16 + step) * step_s
                events.append((at, 1, channel, note, 60 if hit == 'o' else 100))
                events.append((at + min(0.1, step_s * .8), 0, channel, note, 0))
    if not events:
        raise ValueError('Pattern contains no notes')
    return sorted(events), duration, sorted({(g, p) for g, p, _ in rows})


class Playback:
    def __init__(self):
        self._mutex = threading.Lock()
        self._cancel = threading.Event()
        self._thread = None
        self._report = {'status': 'idle'}

    def status(self):
        with self._mutex:
            return dict(self._report)

    def start(self, device, operation_lock, events, duration, assignments, verify=None):
        with self._mutex:
            if self._thread is not None and self._thread.is_alive():
                raise ValueError('An audition is already running; stop it first')
            self._cancel = threading.Event()
            self._report = {'status': 'queued', 'playback_id': uuid.uuid4().hex,
                            'duration_s': duration, 'assignments': assignments}
            self._thread = threading.Thread(target=self._run, args=(device, operation_lock, events, duration, verify),
                                            name='ep133-audition', daemon=True)
            self._thread.start()
            return dict(self._report)

    def _run(self, device, operation_lock, events, duration, verify):
        active = set()
        try:
            with operation_lock:
                if self._cancel.is_set():
                    with self._mutex:
                        self._report['status'] = 'stopped'
                    return
                if verify:
                    verify()
                with self._mutex:
                    self._report['status'] = 'playing'
                started = time.monotonic()
                try:
                    for at, on, channel, note, velocity in events:
                        if self._cancel.wait(max(0, started + at - time.monotonic())):
                            break
                        if on:
                            active.add((channel, note))
                        device.send_midi_note(channel, note, velocity, bool(on))
                        if not on:
                            active.discard((channel, note))
                    else:
                        self._cancel.wait(max(0, started + duration - time.monotonic()))
                finally:
                    failures = []
                    for channel, note in sorted(active):
                        try:
                            device.send_midi_note(channel, note, 0, False)
                        except Exception as e:
                            failures.append(str(e))
                    if failures:
                        raise RuntimeError('Note-off cleanup failed: ' + '; '.join(failures))
                with self._mutex:
                    self._report['status'] = 'stopped' if self._cancel.is_set() else 'finished'
        except Exception as e:
            with self._mutex:
                self._report.update(status='failed', error=type(e).__name__, message=str(e))

    def stop(self):
        with self._mutex:
            self._cancel.set()
            thread = self._thread
        if thread is not None:
            thread.join(timeout=2)
        result = self.status()
        if thread is not None and thread.is_alive():
            result['status'] = 'stopping'
        return result
