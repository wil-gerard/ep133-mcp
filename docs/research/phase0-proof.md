# Phase 0 proof — 2026-09-10

**The write path works and survives a power cycle.** This is the gate the brief
says everything downstream depends on, and it passes.

Device: EP-133 SKU TE032AS001, OS 2.5.1.

## What was proven

| Step | Result |
|---|---|
| Upload a WAV to a free library slot | 91 frames, all status 0 |
| Assign it to group A physical pad "1" (node 7207) | one metadata write, exactly one pad changed |
| Full power cycle (USB out, battery out, screen dark) | — |
| Pad still assigned after boot | `sym` = 16, unchanged |
| Sample still present, audio intact | `crc` = 2727906176, **matches `zlib.crc32` of the local PCM exactly**; `sample.end` = 18750 frames |
| **Pad audibly plays the tone** | **confirmed by the device owner** |

The CRC match is the strong part: the PCM on flash after a power cycle is
byte-identical to the file on disk. And the owner confirming the sound rules out
the failure mode where metadata is correct but the audio is silent or corrupt —
which no amount of metadata reading could have caught.

This satisfies the brief's Phase 0 deliverable: one WAV on pad A1, surviving a
power cycle.

## Finding: a free library slot is not a safe library slot

Uploading to slots 15 and 16 silently armed **two pads in project 6**, which we
never wrote to.

Project 6's pad records `a/p04` and `a/p07` store slot fields 15 and 16 with
length 0. They read as unassigned only because slots 15 and 16 did not exist.
The moment our upload created those slots, both pads began playing our test tone.

```
P06/a/p04: stored slot 15, length 0   -> read 0 before upload, 15 after
P06/a/p07: stored slot 16, length 0   -> read 0 before upload, 16 after
```

This is a **safety requirement for `install_sample`**, not a curiosity:

> Before writing to a library slot, scan every project's pad records for a
> stored slot field matching it. A slot that reads empty can still be
> referenced, and filling it changes what unrelated projects play.

Our own 432-pad comparison already showed 36 dangling slot ids across 63 pads on
this device, so the condition is common, not exotic. The preflight in Phase 1
must report which projects and pads a candidate slot would affect, and treat a
slot with live references as requiring explicit confirmation.

## Finding: writing the project root `active` field does not switch projects

`FILE_METADATA_SET` of `{"active":8000}` on the project root returns status 0 and
the field reads back as 8000, but **the device stayed on project 5** — confirmed
by the owner looking at the hardware. Reverted to 7000 immediately.

So `active` reports state rather than commanding it, at least while running. Do
not treat a successful metadata write as evidence that the device did anything.
Reading `active` to learn the current project (as
[device-baseline.md](device-baseline.md) does) remains valid; writing it is not a
project-switch mechanism, and we do not have one.

## Unresolved: node 7204 drifted back after a verified restore

Node 7204 was set to slot 14 during the pad-addressing tests. The restore
reverted it — verified explicitly, `7204=0`, 0 unexplained across 432 pads. With
no further write to that node, it later read 14 again, and it still reads 14
after the power cycle, so whatever happened reached flash.

The backup stores slot field 341 at `P05/a/p04`, so 14 did not come from the
restored record.

Working hypothesis, **not confirmed**: project 5 is the active project and the
device holds it in RAM; the restore wrote flash, our verification read the
restored flash, and a later flush wrote the stale RAM copy back over it. Project
6 is not active, which would explain why it behaved differently.

If that is right, then **restoring a backup does not reliably revert the active
project**, and the restore result in
[backup-verification.md](backup-verification.md) is weaker than stated there —
it was verified immediately after the restore, which is exactly when this
hypothesis says the reading would still look correct. Recovery may require a
power cycle before verification, or restoring while a different project is
active.

This needs resolving before any tool advertises restore as a safety net. It does
not affect the Phase 0 result above, which was verified across a power cycle.

## Device state left behind

- Slots 15 and 16 both hold the test tone; slot 15 is redundant.
- Project 5 node 7207 (pad "1") assigned to slot 16 — our proof.
- Project 5 node 7204 (pad "4") reads slot 14 — the drift above.
- Project 6 nodes 8204 and 8207 armed by the stale-reference effect.

None of this is destructive, but the device is not in its backed-up state.
Cleaning up means clearing those pads and removing slots 15 and 16 — and we have
not mapped any library delete path yet.
