# Phase 1 implementation validation — 2026-09-10

## Verified offline

- 281 tests pass with `uv run pytest -q`; tests never open device ports.
- A real MCP stdio client initializes the server, lists all eight tools,
  reads device information, validates a backup, installs a two-sample kit,
  rejects reuse of the invalidated backup id, and undoes both assignments.
  The device in this test is fake. Malformed requests are rejected by MCP.
- Upload metadata, all 87 PCM chunks and the terminator reproduce the
  committed slot16 capture byte-for-byte. FILE_INFO is not sent.
- Failure tests cover corrupt archives/audio, missing project records,
  identity/age changes, stale stored references, malformed/truncated WAVs,
  insufficient aggregate memory, no safe slots, confirmation expiry/content
  changes, failed journals, rejected uploads, CRC mismatch, lost assignment
  acknowledgment, device changes during upload, partial kits and durable undo.
- Both local private backups pass the packaged parser: 106 and 108 library
  slots respectively, each with 432 stored pad records. No audio was copied
  into the repository. This is archive validation, not a fresh device comparison.
- `uv build` produces a wheel and source archive for `0.1.0.dev0`. The wheel also imports successfully in an isolated environment. Build outputs
  remain local and are not a published release.

## Hardware evidence already available

Phase 0 demonstrated upload, assignment, CRC equality and persistence after
an owner-operated power cycle. The project TAR reader and `list_pads` were
subsequently checked against all nine projects. These findings support the
protocol implementation; they do **not** certify the new install orchestrator.

See [phase0-proof.md](phase0-proof.md), [upload-capture.md](upload-capture.md),
and [project-tar-read.md](project-tar-read.md).

## Required before release — unverified

Owner attendance and one MIDI owner are required. No new hardware writes were
performed during this implementation session.

1. Stop the server, save a new full Sample Tool backup, close Sample Tool, and
   start this build. Leave the device untouched during all server operations.
2. Run `device_info`, `list_pads` and `verify_backup` through an MCP client.
   Record OS/SKU, counts and sanitized differences, never the serial or audio.
3. Review the intended project/group/pad with the owner. Install the committed
   synthesized `fixtures/phase0-test-tone.wav`. If a confirmation is required,
   obtain owner approval for that exact impact before echoing the token.
4. Save the result and journal id. Verify the returned slot's CRC and the
   stored pad assignment. Stop the server, have the owner power-cycle the
   device, run `uv run python tools/verify_installed_journal.py` with the server still
   stopped to compare slot CRC/frame count and stored pad fields to the journal.
   Then restart the server. The owner
   should trigger the intended physical pad and confirm audible playback.
5. Exercise `undo_last_install`, comparing prior stored slot and length and
   reporting library slots left in place. If the prior record contained a
   stale id, an explicit incomplete-undo result is expected; do not claim it
   restored that record. Make and verify a new full backup afterward.
6. With owner-reviewed destinations and a fresh backup, exercise a small kit.
   Evaluate partial recovery under an owner-approved interruption; record
   which writes landed and which remain uncertain. Do not unplug or power off
   during a transfer without the owner's explicit agreement.
7. Record evidence, resolve failures, then complete Dex tasks `rbv6zfrx`,
   `8zru1ujl` and `fseydiaq`. Publication is separate from release preparation.

## Practical limits

Device scans are sequential, not atomic. State is checked before upload and
again before assignment, but a user or another MIDI application can still
race a read/write boundary. The server lock excludes other instances of this
server, not Sample Tool or unrelated clients. Backup comparison covers stored
slot/length and occupancy, not complete audio or project equivalence. These
limits are stated in the README and tool descriptions rather than hidden by
successful acknowledgments.
