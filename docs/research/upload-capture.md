# Sample upload, captured and verified — 2026-09-10

First authorized audio write. Two uploads of the same synthesised fixture
([`fixtures/phase0-test-tone.wav`](../../fixtures/phase0-test-tone.wav), 0.4 s,
46875 Hz mono 16-bit, 37,500 bytes PCM) to library slots 15 and 16 on EP-133
SKU TE032AS001, OS 2.5.1. No pad was assigned and no project was touched.

Captured frames: [`fixtures/upload-capture-slot16.json`](fixtures/upload-capture-slot16.json)
— 102 frames with every request hex, response status and response payload. The
audio is ours, so the capture is publishable as-is.

## The sequence that works

91 frames, every one status 0:

| # | Frame | Payload |
|---|---|---|
| 1 | `GREET` | empty |
| 2 | `FILE_INIT` **flags=1** (write mode) | max length 4 MiB |
| 3 | `FILE_PUT_META` | name, `data_size` u32 BE, `{"channels":1}`, slot u16 BE |
| 4…90 | `FILE_PUT_DATA` × 87 | 433-byte chunks, last one 262 bytes |
| 91 | terminator | empty `FILE_PUT_DATA` at page 87 |

## FILE_INFO is not required — resolved

[offline-validation.md](offline-validation.md) recorded a contradiction in
ep133-ppak: `upload_sample`'s docstring says "FILE_INFO commits the uploaded
audio buffer to this slot — without it the device discards the upload", while
the sequence it actually sends ends at the terminator and never sends FILE_INFO.

Probing the slot between the two points, inside one session:

| Point | Slot exists? | Free space |
|---|---|---|
| Before upload | no (`status 1`, "invalid file id") | 4,055,172 |
| **After terminator, before FILE_INFO** | **yes, with full metadata** | **4,014,252** |
| After FILE_INFO (`status 0`) | yes, byte-identical metadata | 4,014,252 |

**The terminator commits.** FILE_INFO is accepted but changes nothing
observable — not the slot, not free space, not the metadata. The docstring is
wrong; `upload_sample` is not broken. Worth reporting upstream so nobody else
adds a FILE_INFO call to "fix" a bug that is not there.

Caveat: this shows FILE_INFO is unnecessary for the slot to exist and hold
correct data within the session. It is not yet shown that an upload without
FILE_INFO survives a power cycle — that is the `lr5es6d5` acceptance test.

## The upload is bit-exact

Slot metadata after upload:

```json
{"channels":1,"samplerate":46875,"format":"s16","crc":2727906176,
 "name":"16_testtone","sample.start":0,"sample.end":18750,
 "sound.playmode":"oneshot","sound.amplitude":100,"sound.pan":0,
 "sound.pitch":0.0,"sound.rootnote":60,"time.mode":"off","sound.bpm":0.0,
 "sound.bars":1.0,"envelope.attack":0,"envelope.release":255,
 "sound.loopstart":-1,"sound.loopend":-1}
```

- `sample.end` 18750 equals the fixture's frame count exactly.
- `samplerate` 46875 is preserved — a native-rate file is stored unconverted.
- **`crc` 2727906176 equals `zlib.crc32` of the local PCM exactly.** The device's
  `crc` field is a standard CRC-32 over the raw PCM payload.

That last point is the useful one for Phase 1: **an install can be verified
without downloading the sample back.** Compute CRC-32 locally, compare against
the slot's `crc`, and a mismatch is proof of corruption. This should be the
verification step in `install_sample` rather than trusting an ACK.

## Slot occupancy is the response status

An occupied slot answers `FILE_METADATA_GET` with status 0; an empty one answers
status 1 with the body `invalid file id`. Do **not** infer emptiness from failing
to parse the metadata: a populated slot's record can exceed one page, and an
earlier version of our own preflight guard would have cleared an occupied slot
for overwrite because of exactly that. Metadata reads must walk pages until the
null terminator.

## Memory accounting

Both uploads cost **40,920 bytes** of free space for 37,500 bytes of PCM — 3,420
bytes of overhead per sample, reproducible across two uploads. That does not
match the ~1,583 B/sample average inferred from the backup-vs-capacity residual,
so per-sample overhead is not a single constant and probably depends on length
or block alignment. Not resolved. Phase 1 preflight should treat free space as
authoritative and re-read it rather than predicting the cost of a write.

## Device state left behind

Slots 15 and 16 both hold the test tone. Slot 15 was written by the first run,
whose frame capture was lost to a path bug after the session closed; slot 16 is
the run that produced the retained fixture. Slot 15 is redundant and should be
removed, or cleared by restoring the verified backup, before this device is
considered clean.

## Not yet established

- That the upload survives a power cycle.
- That a pad assigned to slot 16 plays it.
- Any delete or overwrite path for a library slot.
