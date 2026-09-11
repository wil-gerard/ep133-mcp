# Live project TAR verification

## Verified: first read, 2026-09-10

Owner confirmed presence and that Sample Tool was closed. No other EP-133
script/server process was found. One DeviceSession opened project 1 only,
known file id 3000, after GREET and read-mode FILE_INIT. Open returned status 0.
No device writes were sent.

The local 2026-09-11 `.pak` passed its SHA-256 sidecar check before access.
The read returned 98,816 bytes, exactly equal to `/projects/P01.tar` in that
backup. TAR SHA-256:
`092d62423b18fb5f1399bd89b171418fd4bba2012307ef21260af5602130a09d`.

Pages contain 324 data bytes. After the existing response parser removes the
status byte, the payload starts with a two-byte big-endian page index (observed
`0000`, `0001`, `0002`). Thus upstream's three-byte header includes status;
stripping three bytes from our parsed payload corrupts the archive. A short
page terminates the read. Timeouts and rejected pages are failures, not EOF.

Raw pages remain private at `/tmp/ep133-project1-pages.json`; no project content
or backup is committed. This proves project 1 only. All-project stored pad
comparison remains pending. Unknown file ids and delete remain untested.

## Verified: production reader and list_pads, same session

`tools/verify_project_reads.py` checks the sidecar, then uses one DeviceSession
for all nine known project ids. Each project is read once for full TAR equality
and again through `list_pads`, comparing every stored slot/length and stale flag
against the backup. No inferred empty pads or timeout-as-EOF behavior.

Completed results (full TAR equality and all 48 pads agree in each row):

| Project | TAR bytes | Nonzero stored references absent from library |
|---|---:|---:|
| 1 | 98,816 | 19 |
| 2 | 82,432 | 0 |
| 3 | 116,224 | 37 |
| 4 | 100,864 | 33 |
| 5 | 62,976 | 26 |
| 6 | 60,928 | 5 |
| 7 | 91,648 | 23 |
| 8 | 105,472 | 13 |
| 9 | 118,784 | 14 |

`stale_reference` includes nonzero stored ids with zero record length, as
required by the stored-slot contract. It does not label slot 0 as stale.
These counts therefore differ from the earlier 63 nonzero-length pads with
stale ids; length is returned separately. Zero-length references also matter
to conservative slot selection. The backup has 63 nonzero-length stale pads.

The original baseline test suite opened the device for its `device_info`
round-trip before Sample Tool closure was confirmed. This was an existing test
behavior missed during the initial inspection; it made no writes. That test
now uses a fake unavailable session, so routine tests no longer open the port.

All nine TARs match byte-for-byte; all 432 stored pad records and stale flags
agree. The verification session closed normally. There were no rejected opens,
timeouts, writes, or power cycles during the project verification run.

The first manual MCP client completed the tool call but its result inspection
failed: this SDK exposes `structured_content`, not `structuredContent`.
The subprocess exited. This was a verification-script error, not a device
rejection; repeat with the correct SDK field is required.

The corrected real MCP stdio round-trip passed: `list_pads({})` returned active
project 4 and 48 pads. The client and server exited normally and released MIDI.
Offline suite: 231 passed. `git diff --check` passed.
