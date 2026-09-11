# Full backup verification — 2026-09-09

The full sounds-and-projects backup required by [hardware proof](hardware-proof.md)
step 3 now exists and has been verified against the live device.

Everything up to "Pad addressing" below is read-only — `GREET`, read-mode
`FILE_INIT` (flags 0) and `FILE_METADATA_GET`. The pad-addressing section rests
on two authorized writes, each a single `FILE_METADATA_SET` of `{"sym":14}` to a
pad the device and the backup both reported empty. No sample was uploaded, no
project file was written, no firmware command was sent.

## Artifact

| Field | Value |
|---|---|
| Filename | `EP-133_<serial>_2026-09-09_backup.pak` |
| Size | 53,076,843 bytes |
| SHA-256 | `121da34ac90fadd5772ad9c956aa4cc7d3e7a3e625dc883f294bc330b42d02fb` |
| Private copy | `~/Documents/ep133-backups/` (outside Git, with a `.sha256` sidecar) |
| Produced by | EP Sample Tool, `pak_release` 1.2.0, `pak_version` 1 |

The serial number appears in the real filename and is omitted here. `*.pak` is
gitignored; the archive must never enter this repository.

## Archive structure and readability

The `.pak` is a **ZIP** archive (not a TAR), 116 entries:

```
/meta.json                 298 B
/projects/P01.tar … P09.tar  9 files,  838,144 B total
/sounds/NNN <name>.wav     106 files, 58,589,218 B total
```

- `unzip -t` reports no errors.
- All 106 WAVs parse with Python's `wave` module: 98 stereo/16-bit/44.1 kHz,
  6 mono/16-bit/44.1 kHz, 2 mono/16-bit/22.05 kHz.
- Every archive entry name begins with `/`. Any extractor we write must strip
  the leading slash and reject path traversal rather than trusting the name.

`meta.json` matches the [device baseline](device-baseline.md) exactly:
`device_name` EP-133, `device_sku` / `base_sku` TE032AS001, `device_version`
2.5.1, `pak_type` "user".

## Completeness

| Quantity | Bytes |
|---|---|
| Device used (`max_capacity` − `free_space_in_bytes`) | 58,757,028 |
| Sound payload in the backup | 58,589,218 |
| Difference | 167,810 (0.29%, ≈1,583 B per sound) |

The residual is consistent with per-sample store overhead or block alignment and
is far too small to hide an omitted sample. The backup covers the whole sample
store. The exact cause of the 167,810 B residual is **not** established.

The archive stores 44.1 kHz WAVs while the device reports `samplerate.native`
46875. Resolved 2026-09-10: reading slot metadata directly shows the **device
itself** stores `"samplerate":44100` for those samples (slot 14, `090grave`,
`"channels":2,"format":"s16"`). So 44.1 kHz is the stored rate, not something
Sample Tool introduced on export, and `samplerate.native` is the rate the device
*records* at, not a rate it forces on imports — consistent with the advertised
`samplerate.range` of 1–65535. The export looks faithful.

This does not by itself prove the audio round-trip is bit-exact; it removes the
specific reason to think it is lossy.

## Verification against the live device

All 432 pads (9 projects × 4 groups × 12 pads) were read from the device and
compared with the slot ids decoded from the project TARs:

| Outcome | Pads |
|---|---|
| Live `sym` equals the backup slot id | 369 |
| Backup slot id refers to a library slot that no longer exists; live `sym` = 0 | 63 |
| Unexplained mismatch | **0** |

The 63 explained cases involve 36 distinct dangling slot ids (19, 33–38, 53,
100, 109, 114, 130, 132, 137, 138, 204, 214, 217, 218, 229, 240, 309, 321, 325,
326, 337, 405, 439, 445, 448, 504, 513, 514, 558, 612, 614). This is a real
behaviour worth designing around: **a project TAR retains the slot id of a
sample that has been deleted from the library, while the device reports the pad
as unassigned.** It confirms the brief's warning that library slots are shared
across projects.

Sanitized snapshot: [`fixtures/pad-sym-snapshot-2026-09-09.json`](fixtures/pad-sym-snapshot-2026-09-09.json)
(slot ids only — no sample names or audio).

## Pad addressing, resolved on hardware

Three numbering schemes were in play. Two authorized single-pad writes plus the
432-pad comparison pin them down, and the answer is that **there is only one
numbering**: the metadata node suffix, the SysEx `pad_num` and the project TAR's
`pNN` all use the same index — visual position, top-to-bottom and left-to-right
(1 = "7", 4 = "4", 7 = "1", 10 = ".").

Evidence, in two parts:

1. *Node suffix corresponds to TAR `pNN`*, from the 432-pad comparison. Scoring
   both candidate mappings against one live snapshot:

   | Hypothesis | Agree | Dangling | Unexplained |
   |---|---|---|---|
   | node `NN` == TAR `pNN` | 369 | 63 | **0** |
   | node `NN` == row-reversed TAR `pNN` | 254 | 60 | 118 |

2. *Node suffix corresponds to the physical pad*, from hardware. Writing
   `{"sym":14}` to node 7204 loaded the pad labelled **"4"**; writing it to node
   7207 loaded the pad labelled **"1"**. Both match the SysEx `pad_num` table in
   ep133-ppak's `PROTOCOL.md` §3.

Consequences:

- ep133-ppak's `pad_file_id()` and `assign_pad()` are correct as written.
- **`PROTOCOL.md` §3's TAR↔SysEx translation table is wrong.** It describes the
  project TAR as bottom-up — `pads/c/p01` as the bottom-left pad — and prescribes
  a row-reversing translation between TAR and SysEx numbering. A Sample Tool
  export uses no such reversal: `pads/a/p04` is the pad labelled "4", not "1".
  Code that applies the documented translation while patching a `.ppak` places
  every pad in the wrong row. This is the highest-value thing we have to send
  upstream.
- The original baseline claim — "A1 is physical label 1, SysEx position 7, node
  7207" — is correct. An interim revision of this document asserted node 7204
  instead, reasoning from `PROTOCOL.md`'s TAR table; that was wrong and is
  retracted.

Scope: verified for *reading* device metadata and for *reading* a Sample Tool
export. Whether the device applies the same numbering when *importing* a
hand-built `.ppak` is untested, and is a Phase 2 gate.

## Correction: the pad-record slot field is u16, not u8

`PROTOCOL.md` §7 lists offset 1 as `slot u8` and offset 2 as `midiChannel`. This
device's library holds slots up to 935, which a u8 cannot address. Decoding
offsets 1–2 as **u16 LE** resolves every assigned pad to a slot the device
confirms: all 71 pads the device reports as occupied match the u16 reading, and
the 9 occupied pads of the active project match on both the live read and the
backup (slots 9, 10, 460, 495, 505, 505, 509, 932, 933).

A pad is "assigned" when the u32 LE at offsets 8–11 is non-zero; unassigned pads
can carry non-zero bytes at offsets 1–2, so the length field is the reliable
occupancy test in the TAR.

This is a candidate upstream issue for `ep133-ppak`, per the brief's rule to
report disagreements rather than silently fork. Not yet filed.

## Record size anomaly

`PROTOCOL.md` §7.0 states pad records are 26 bytes factory-native and that
Sample Tool emits a 27-byte round-trip form. This single Sample Tool export
contains **both**: 220 records of 27 bytes and 212 of 26 bytes, mixed within the
same project (P05 group A has 27-byte and 26-byte records side by side). The
26-byte records are the 27-byte form minus a trailing `0x00`.

No rule separating the two forms has been identified. Because the erratum ties
the 27-byte stride to `ERR PATTERN 189` on scene-switch iteration, this needs to
be understood before we emit project files. Recorded as an open question, not
resolved.

## Restore, demonstrated

> **Caveat added 2026-09-10.** A pad this restore reverted later drifted back to
> its pre-restore value and persisted across a power cycle. The restore may not
> reliably revert the *active* project, and the verification below ran
> immediately after the restore — exactly when that failure would still look
> correct. See [phase0-proof.md](phase0-proof.md). Treat the result below as
> demonstrated for non-active projects and unconfirmed for the active one.

Restore was demonstrated on 2026-09-09 against a real, deliberate difference.
Two pads in project 5 (nodes 7204 and 7207) had been set to slot 14 by the
authorized pad-addressing writes above; the device therefore differed from the
backup in exactly two known places. The owner restored the `.pak` through EP
Sample Tool, and [`tools/verify_against_backup.py`](../../tools/verify_against_backup.py)
re-read all 432 pads:

| Outcome | Pads |
|---|---|
| Agree with the backup | 369 |
| Dangling (backup slot deleted from library, device reports empty) | 63 |
| Unexplained | **0** |

Both modified nodes returned to `sym` = 0, and `max_capacity` / `free_space_in_bytes`
came back identical to the pre-restore reading (62,853,120 / 4,096,092).

This is a genuine recovery demonstration rather than a no-op: restoring onto a
device that already matched the backup would have been indistinguishable from a
restore that silently did nothing, which is why the two pads were left dirty on
purpose.

What it establishes: a `.pak` produced by Sample Tool can be loaded back, it
reverts pad assignments to their backed-up values, and it **preserves library
slot ids** — the 369 agreements are all slot-id matches, so the fear that
restore might renumber slots and shift every project's pad references is not
borne out here.

Still open:

- **Replace vs. merge is undetermined.** The library after restore matches the
  backup exactly, but the library before restore also matched it (only pad
  metadata had changed), so this run cannot distinguish a wholesale replace from
  a merge. Deciding it needs a restore performed while the library differs.
- The 63 dangling references survived the restore unchanged, which is consistent
  with restore reproducing the backup faithfully rather than repairing state.
- Audio round-trip bit-exactness is still untested; pad metadata is what was
  checked here. The 44.1 kHz concern is resolved (see Completeness above) — the
  device stores that rate itself — but that is not the same as proving restore
  returns identical PCM.
- Replace-vs-merge is still undetermined (see above); the confirmation dialog's
  wording suggests replace, but wording is not evidence.

### Restore procedure (as performed)

In EP Sample Tool: **backup and restore → restore local backup**, pick the
`.pak`, then confirm a prompt warning that content may be overwritten, which
requires **typing `OVERWRITE` into a field** before it will proceed.

That is a deliberate typed-confirmation gate, not a single misclick — worth
matching rather than weakening in our own destructive tools. It is also the
model the brief already asks for: explicit confirmation naming the affected
data, never bundled into a batch. Nothing we build should automate past it or
synthesise that keystroke on the user's behalf.

## What this does and does not establish

Established: a complete, readable, checksummed full backup that matches live
device state pad-for-pad; pad addressing pinned down in all three numbering
schemes, with a concrete error found in upstream's TAR translation table; a
corrected slot field width; a documented dangling-slot behaviour; and that a
single-pad metadata write lands on the intended physical pad and reads back.

Also established: restoring the backup reverts pad assignments to their backed-up
values, preserves library slot ids, and leaves the rest of the device untouched.

Not established: that restore is lossless for audio, whether restore replaces or
merges the library, that a pad assignment survives a power cycle, or that any
sample-upload path functions — only pad metadata has been written so far.
