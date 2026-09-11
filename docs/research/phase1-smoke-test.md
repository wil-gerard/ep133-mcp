# Phase 1 server smoke test — 2026-09-10

Owner authorized the minimum single-sample install/power-cycle/playback test.
Sample Tool was not open in Chrome and the server held the device process lock.
All device operations below used the packaged `DeviceSession`; the smoke test
itself uses a real MCP stdio client and the shipped server.

## Backup gate correction — verified on hardware

The first verification matched all 432 stored slot/length pairs but reported
one extra library slot: 0. No writes occurred.

A read-only metadata check returned `{}` for node 0 (successful status), a
normal audio metadata record for slot 1, and the absent-file response for 999.
Together with the established `sym=0` empty assignment and both Sample Tool
backups omitting sound 0, this identifies node 0 as the empty sentinel rather
than an allocatable sound slot. The library scan now covers 1..999 and first
requires node 0 metadata to be exactly `{}`. Unexpected slot-zero content is
a verification failure, not silently ignored. Occupancy for 1..999 remains
status-based and never depends on JSON parsing.

The regression test models status-success for slot 0 and confirms it cannot
make a valid backup appear stale; unexpected sentinel content fails closed.

## Single-sample install, power cycle and playback — verified on hardware

Run 23:14–23:25 local with the owner present. Every device operation went
through the shipped server (`uv run ep133-mcp`) from a real MCP stdio client,
one server process per step. Serial and audio were never printed or stored.

Before the run, a leftover client from the earlier attempt (started 22:27,
blocked on `input()` in another terminal) still held the MIDI port. The server
reported `DeviceUnavailable` with the "another process may own the port" next
step instead of sharing the port; the stale client had written nothing and was
terminated before continuing. This is the lock behaving as designed.

**Read-only** — `device_info`: TE032AS001, OS 2.5.1, 62,853,120 B capacity,
4,014,252 B free, active project 4. `verify_backup` on the 2026‑09‑11 Sample
Tool backup (taken 20:23, no writes since): `current`, 0 differences, 432 pads
compared, with the corrected 1..999 library scan. No fresh backup was taken
because the tool's purpose is to prove currency; it did.
`list_pads(5)` showed group D pads 2–12 with stored slot 0, length 0, `sym` 0
and no stale flag. The owner chose project 5, group D, pad 7 (label "1").

**Write** — In a new server process, `verify_backup` returned the same
`backup_id` (`8f582ff8…f406`). `install_sample` with
`fixtures/phase0-test-tone.wav` returned `installed` directly: the pad was
empty, so `destructive` was false and no confirmation token was issued.
Journal `c6035922a1894c429185cde8b122946e`: node 7507, library slot 17
(first free slot after 16), CRC 2727906176, 18750 frames, prior 0/0. The
server's own post-write checks (slot metadata CRC/`sample.end`, stored pad
record, whole-device snapshot before assignment) passed. The response carried
`power_cycle_verified: false` and `transactional: false` as designed.

**Persistence** — Server stopped; the owner performed a full power cycle.
`tools/verify_installed_journal.py` (read-only, same process lock) reported
`matched`: slot 17 CRC 2727906176 and 18750 frames, stored pad record
(17, 18750) at project 5 / D / pad 7, equal to the journal.

**Playback** — The owner selected project 5, group D and pressed pad "1";
the test tone played. Owner observation, not machine evidence.

Not exercised: `undo_last_install`, `install_kit` with more than one entry,
the confirmation path, and any interruption. Those remain release gates. The
2026‑09‑11 backup is now stale by construction (slot 17 and node 7507 differ);
any further install needs a new Sample Tool backup first.
