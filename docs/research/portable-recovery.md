# Portable projects, recovery and audition — 2026-09-12

## Implemented

- `export_project`: backup → one original project TAR and only its referenced
  WAVs. Missing dependencies and output overwrites are refused.
- `list_samples` / `sample_usage`: metadata, all stored references across nine
  projects, stale references and unreferenced slots, from the device or a backup.
- `check_ppak` / `import_ppak`: included samples, exact PCM/metadata reuse,
  explicit slot remapping, capacity checks, impact-bound confirmation, uploads
  before project replacement and durable partial-failure reports.
- `restore_samples`: original-slot recovery; occupied differing audio or metadata
  is refused. References that will resolve after restoration appear in the impact.
- `restore_project`: one project and its dependencies from a backup through the
  same import path, optionally with samples excluded.
- `undo_last_import`: exact private project preimages, backup integrity checks,
  refusal of active or subsequently edited projects. Uploaded samples remain.
- `set_active_project`: explicit confirmed metadata write at node 2000 and
  read-back; previous project journalled.
- `play_pattern`, `stop_playback`, `playback_status`: bounded asynchronous MIDI
  audition, simultaneous notes, cancellation and note-off cleanup. Accepts the
  existing steps pattern shape. MIDI routing is supplied explicitly; actual
  active-project sample assignments are checked before starting.

Project verification now compares every TAR member's bytes, including unknown
settings fields; matching only the decoded subset no longer establishes success.
Incremental backups reuse unchanged PCM but regenerate the WAV from current
metadata, avoiding stale names and sound parameters in recovery artifacts.

## Decisions

Keep the existing Python/MCP structure and dependencies. Pure archive work belongs
in `protocol/` and `safety/library.py`; shared sample restore validation and writes
belong in `safety/recovery.py`; `server.py` owns MCP adaptation and operation
serialization. Playback has one worker, owns the operation lock while running and
allows stop/status without that lock. Files, journals and recovery caches remain
local and private. No firmware functionality or automatic library overwrite.

Reuse protocol observations, independently implemented here. Feature inspiration:
[ep-unity](https://github.com/seajaysec/ep-unity) for dependency-limited packages;
[mcp-koii](https://github.com/benjaminr/mcp-koii) for live pattern audition.
No code was copied from those projects. The project-selection command comes from
our existing [Sample Tool bundle research](project-write.md).

## Automated acceptance

Tests cover archive fidelity, missing dependencies, malformed WAVs, explicit
remapping, exact audio reuse, sample metadata, capacity refusal, interrupted
uploads, full project comparison, stale confirmation tokens, active-target and
backup-integrity refusal, exact preimage undo even with a superset backup,
simultaneous MIDI events, cancellation, send failures, note cleanup and project
selection read-back. The real stdio MCP handshake includes all 40 tools.

Final automated verification: **514 tests passed**; `uv build` produced the source
distribution and wheel. Commit references and remaining hardware work are recorded
in dex epic `jvbk8wqh`.

## Hardware acceptance — blocked, not claimed

The MIDI backend lists one EP-133 input and output. Opening a session fails at
our ownership lock: the already-running `ep133-mcp` process (PID 19943 at the time
of inspection) owns `~/.local/state/ep133-mcp/device.lock`. It was not terminated
or bypassed, and this implementation session made **no device writes**.

Once the existing connection releases the port:

1. Inspect identity, active project and inventory; create and verify a fresh full
   backup, keeping all archives outside the repository.
2. Export a non-active project with its samples; validate dependencies against
   the backup. Choose a small sample and an unoccupied, unreferenced slot for
   the sample-inclusive import test; inspect the exact impact before applying.
3. Import a reversible BPM change into that project, verify every member, undo
   and compare with the exact preimage. Test a partial-failure path offline only.
4. Exercise selective sample restoration using a disposable, backed-up test
   slot; verify PCM and TNGE metadata. Do not delete user audio to create a test.
5. Switch projects and switch back, verifying the active value each time.
6. With recording off and explicit MIDI routing supplied, audition a short
   pattern, stop mid-note and confirm silence. Compare final backup with the
   baseline and account for any retained test sample.
7. Owner power-cycle and listening checks remain necessary for persistence and
   audibility. Neither can be inferred from automated read-back.

## Supported limits

Sample restoration accepts nonempty mono 46875 Hz, 16-bit PCM WAVs, supported
TNGE fields and 1–20 printable ASCII names. Unsupported formats or sampler chunks
without TNGE are refused; no silent resampling or metadata conversion occurs.
Restoration is sequential, not atomic. Full-device restore still uses Sample
Tool. The tool cannot determine whether the sequencer is recording or read its
global MIDI routing. Live soft hits use velocity 60; exported step patterns retain
the existing velocity encoding limitation.
