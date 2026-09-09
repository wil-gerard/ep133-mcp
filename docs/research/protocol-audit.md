# Prior-art and protocol audit

Audited 2026-09-07 (America/Chicago). Scope: source inspection of six pinned repositories, not hardware verification. No upstream application or device-write command was executed. Upstream test files were initially inspected, not run; the [2026-09-08 follow-up](offline-validation.md) records 49 passing offline tests. The original brief is a proposal; its protocol claims are not current evidence.

## Source inventory

| Repository | Inspected revision | License evidence | Useful component / limit |
|---|---|---|---|
| [icherniukh/ep133-krate](https://github.com/icherniukh/ep133-krate/tree/6f2a85b844387418a98f948cbd61431365c344ae) | `6f2a85b844387418a98f948cbd61431365c344ae` | MIT, root LICENSE | Capture fixtures, wire encoding, upload/download and session recovery; backup helper copies one sample, not the full device. |
| [ZacharySBrown/ep133-ppak](https://github.com/ZacharySBrown/ep133-ppak/tree/93f0f6b1f6110701e6378e2702f39fd7e9a9d687) | `93f0f6b1f6110701e6378e2702f39fd7e9a9d687` | MIT, root LICENSE | Python upload, live assignment, archive and song builders; identification API is incomplete and high-level writes lack our backup/confirmation gates. |
| [garrettjwilke/ep_133_sample_tool](https://github.com/garrettjwilke/ep_133_sample_tool/tree/134ca1fd75d6cfda3c77955104103a60bbf756cb) | `134ca1fd75d6cfda3c77955104103a60bbf756cb` | No root license or package license declaration found | Offline Sample Tool wrapper and MIDI logging; README says unmaintained and points to pbarilla fork. Referenced standalone transfer implementation is a separate repo, ep_133_sysex_thingy. |
| [phones24/ep133-export-to-daw](https://github.com/phones24/ep133-export-to-daw/tree/db8f4398792ad9d2dceec825033514c0453905d0) | `db8f4398792ad9d2dceec825033514c0453905d0` | AGPL-3.0, root LICENSE | Filesystem, GREET metadata, archive and pattern readers. Keep source attribution and license provenance distinct from MIT dependencies. |
| [DannyDesert/EP133-skill](https://github.com/DannyDesert/EP133-skill/tree/2e6458ef7cc0a8458c5e7b081b877df48c408b88) | `2e6458ef7cc0a8458c5e7b081b877df48c408b88` | MIT, root LICENSE | Genre examples and archive builder; binary/event/settings assumptions disagree with newer sources. |
| [benjaminr/mcp-koii](https://github.com/benjaminr/mcp-koii/tree/652793d684be8b9c30698ebf8857c032b6bb427d) | `652793d684be8b9c30698ebf8857c032b6bb427d` | README/setup claim MIT; referenced LICENSE file absent | Live MIDI and readable pattern syntax; default sound/pad configuration is not device inventory. |

## Evidence matrix for the A1 proof

Status meanings: **source + capture** means inspected upstream implementation and referenced capture evidence; **source** means code exists; **experiment required** means this device remains unverified. None means tested on our hardware.

| Operation | Evidence and status | Required experiment / decision |
|---|---|---|
| Handshake / identity | [ep133-krate/src/core/client.py](https://github.com/icherniukh/ep133-krate/blob/6f2a85b844387418a98f948cbd61431365c344ae/src/core/client.py) `_initialize` parses product metadata; [ep133-export-to-daw/src/lib/midi/device.ts](https://github.com/phones24/ep133-export-to-daw/blob/db8f4398792ad9d2dceec825033514c0453905d0/src/lib/midi/device.ts) uses GREET to read SKU. Source. | Record SKU and OS from a read-only session; do not use ep133-ppak identify(), which currently returns empty bytes. |
| Sample inventory | [ep133-krate/docs/protocol-evidence.md](https://github.com/icherniukh/ep133-krate/blob/6f2a85b844387418a98f948cbd61431365c344ae/docs/protocol-evidence.md) identifies filesystem listing and node metadata as preferable to stale legacy GET_META. Source + capture. | Cross-check listed occupied/free slots with Sample Tool before selecting a destination. |
| Memory capacity / free space | [ep133-krate/README.md](https://github.com/icherniukh/ep133-krate/blob/6f2a85b844387418a98f948cbd61431365c344ae/README.md) identifies unresolved memory request and fallback capacity. Experiment required. | Capture the Sample Tool memory request/response and verify units; never infer available capacity from sample durations or assume 64/128 MB. |
| Full backup and recovery | [ep_133_sample_tool/README.md](https://github.com/garrettjwilke/ep_133_sample_tool/blob/134ca1fd75d6cfda3c77955104103a60bbf756cb/README.md) describes full vs projects-only backup; [ep133-krate/src/core/backup.py](https://github.com/icherniukh/ep133-krate/blob/6f2a85b844387418a98f948cbd61431365c344ae/src/core/backup.py) only preserves a downloaded sample. Experiment required. | Obtain full sounds-and-projects backup, checksum it and verify archive contents. Establish restore procedure before writes; a sample copy or project-only export fails this gate. |
| PCM upload | [ep133-krate/docs/protocol-evidence.md](https://github.com/icherniukh/ep133-krate/blob/6f2a85b844387418a98f948cbd61431365c344ae/docs/protocol-evidence.md) links official upload fixtures; [ep133-ppak/ep133/transfer.py](https://github.com/ZacharySBrown/ep133-ppak/blob/93f0f6b1f6110701e6378e2702f39fd7e9a9d687/ep133/transfer.py) builds upload messages. Source + capture. | Compare emitted frames, acknowledgements and resulting PCM with a real sample upload. Include finalize/commit and session reset. |
| Pad assignment | [ep133-ppak/ep133/client.py](https://github.com/ZacharySBrown/ep133-ppak/blob/93f0f6b1f6110701e6378e2702f39fd7e9a9d687/ep133/client.py) assign_pad calls metadata SET; [ep133-krate/docs/protocol-evidence.md](https://github.com/icherniukh/ep133-krate/blob/6f2a85b844387418a98f948cbd61431365c344ae/docs/protocol-evidence.md) captures sym writes to pad nodes. Source + capture. | Check project, physical pad label and sample slot separately, then read back assignment. A1 means group A, physical label 1 for this plan, not top-left position 1. |
| Pad address translation | Resolved on our hardware by two authorized single-pad writes plus a 432-pad read comparison: see [backup verification](backup-verification.md). | **Settled.** Metadata node suffix, SysEx `pad_num` and TAR `pNN` are all one numbering (visual, top-to-bottom). Physical label 1 = `pad_num` 7 = node 7207 in project 5. PROTOCOL.md §3's TAR↔SysEx translation table is wrong and should not be applied. |
| Persistence | [ep133-ppak/tools/load_one.py](https://github.com/ZacharySBrown/ep133-ppak/blob/93f0f6b1f6110701e6378e2702f39fd7e9a9d687/tools/load_one.py) uploads and assigns but does not establish our power-cycle acceptance criterion. Experiment required. | Power-cycle after upload, verify assignment and audio again; successful ACK alone is insufficient. |
| Failure / recovery | [ep133-krate/docs/protocol-evidence.md](https://github.com/icherniukh/ep133-krate/blob/6f2a85b844387418a98f948cbd61431365c344ae/docs/protocol-evidence.md) documents download session reset and metadata loss risks. Source + capture. | Verify timeout handling and recovery using authorized test data; do not automatically replay a partially completed write. |

## Corrections to the brief

- Pad numbering is uniform: metadata node suffix, SysEx `pad_num` and project TAR `pNN` all use the same visual top-to-bottom index. ep133-ppak's `assign_pad` is right; its PROTOCOL.md §3 TAR↔SysEx translation table is wrong, and applying it misplaces every pad by a row. Verified on hardware; see [backup verification](backup-verification.md).
- The pad-record slot field is u16 LE at offsets 1-2, not the u8 at offset 1 that PROTOCOL.md documents. Slots above 255 exist on this device. A pad is assigned when the u32 LE at offsets 8-11 is non-zero.
- A single Sample Tool export contains both 26-byte and 27-byte pad records, mixed within one project. PROTOCOL.md's erratum treats these as two separate provenances. Unresolved.
- Live pad assignment is implemented via metadata SET. An unknown raw project-file write path does not imply all per-pad writes require a .ppak round trip.
- `03 00` is documented as FILE_READ_OPEN in the current ep133-ppak command table. Do not probe it as a write command.
- Pattern and scene generation now exist in ep133-ppak, and phones24 reads sequencer events. The protocol document still lists storage as unknown, so source and prose disagree. Keep independent import/playback verification as the composition gate; retain song mode as a deliberate product non-goal.
- ep133-ppak's song builders use 26-byte pads and BPM float32. Its older protocol discussion and DannyDesert's settings/event generation disagree in places. Optional factory and full-song fixtures are absent unless supplied separately; self-round-trip tests alone do not resolve hardware correctness.
- Krate's README says device_info always returns None, but its current client parses initialization metadata. Check source and device behavior rather than propagating the README claim.
- The original Sample Tool wrapper is unmaintained. Its README points to a maintained fork and a separate transfer repo; neither additional repo was audited here.
- The stack is mixed: Python libraries plus TypeScript/Electron references. Do not claim all ecosystem tools use Python.
- Claims about market uniqueness, current OS capabilities, maximum sample durations and unit capacity were not independently established by this audit. Treat them as unverified until the relevant device/manual evidence is collected.

## Reuse recommendation

Start Phase 0 from ep133-ppak's upload/assignment implementation and compare with krate's captured transactions and session handling. Do not wrap the entire upstream CLI: it can overwrite without our preflight, backup or confirmation. Do not copy bundled vendor assets from the Sample Tool wrapper. Preserve exact license and source attribution for any code eventually reused; no upstream code was vendored during this audit.

The most useful next work is a read-only baseline and full backup. There is enough upstream evidence to attempt the proof after that gate; there is not enough evidence to mark the proof complete or begin MCP implementation.

## Local hardware check

`system_profiler SPUSBDataType -json` returned an empty device list in this session. This does not prove physical absence, but it supplies no evidence of an accessible EP-133. No MIDI messages were sent. Connection, baseline backup, capture, A1 assignment and power-cycle verification remain pending.

## Follow-up

See [offline validation](offline-validation.md) for captured-upload/assignment test results, an unresolved FILE_INFO finalization discrepancy, and the more conclusive USB/MIDI endpoint checks.

## Connected-device follow-up

The [device baseline](device-baseline.md) verifies GREET, sample-root capacity/free-space metadata and all 48 active-project pad reads on OS 2.0.2. It supersedes the earlier connection and memory-query blockers for this device. Full backup and write/persistence proof remain pending.
