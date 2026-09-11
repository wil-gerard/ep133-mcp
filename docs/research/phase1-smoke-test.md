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
