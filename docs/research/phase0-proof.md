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

## Finding: the project root `active` field switches projects at boot, not live

`FILE_METADATA_SET` of `{"active":8000}` on the project root returns status 0 and
reads back, but the device **does not switch while running** — verified by
looking at the hardware, which stayed on project 5.

After a full power cycle, the device **booted into project 6**. So `active` is a
boot-time project selector. This is the project-switch mechanism; it just costs a
reboot.

The field is also maintained by the device: after the owner changed the project
by hand to project 4, `active` read 6000 without us writing anything.

That independently re-confirms the project addressing from a third direction:
`base = 2000 + N × 1000`, so 6000 is project 4, 7000 is project 5, 8000 is
project 6. This agrees with the 432-pad comparison, where `P05.tar` matched base
7000.

Two practical consequences:

- A successful metadata write is not evidence that the device acted on it. Some
  fields are latched at boot. Read-back confirms storage, not effect.
- Because the device writes `active` itself, the project root is not a
  device-read-only surface. Anything we build must expect the device to change
  state underneath us while a session is open.

An earlier revision of this document claimed writing `active` does not switch
projects at all. That was wrong — it was written before the power-cycle test,
from the live-write result alone.

## Resolved: the restore did not revert node 7204, and our check could not have seen it

Node 7204 was set to slot 14 during the pad-addressing tests. The restore was
verified immediately afterwards — `7204=0`, 0 unexplained across 432 pads — and
that check passed. Later the node read 14 again, and persisted through a power
cycle.

A fresh backup taken 2026-09-10 settles it. Diffing pad records against the
2026-09-09 backup with [`tools/diff_backups.py`](../../tools/diff_backups.py):

```
P05/a/p04 (node 7204)
  2026-09-09 backup: slot=341  len=0        (the state the restore should have written)
  2026-09-10 backup: slot=14   len=470400   (our write, never reverted)
```

**The restore did not revert this record.** The stored slot field is the 14 we
wrote, not the 341 in the backup being restored. Everything else across all nine
projects is byte-identical to the old backup except node 7207 (which we later
assigned deliberately) and project 5's `scenes` file (which we never touched —
see below).

### Why the verification passed anyway

A live `FILE_METADATA_GET` returns the *resolved* `sym`, not the stored slot
field. A stored id whose slot does not currently exist reads as 0 — exactly like
an empty pad. So at the moment we verified, node 7204 resolved to 0 despite
holding 14, and the check treated that as proof of reversion. **It was not.**
The most likely reason it resolved to 0 at that instant is that the sound
library had not finished restoring, so slot 14 did not yet exist; it read 14
again once it did.

That means the method was unsound, not just one pad unlucky. **A restore cannot
be verified by reading `sym` over SysEx.** The only sound check is to take a
fresh backup after the restore and diff the pad records against the backup that
was restored.

### What is still not explained

Node 7207 was written to 14 in the same session, restored the same way, and read
0 afterwards *and* stayed 0 until we deliberately assigned 16 to it. So the
restore reverted one of two identically-treated pads. No mechanism for that has
been established. Candidates: the restore skipping records that match some
cached state, or the active project being re-flushed from RAM after the restore
in a way that happened to cover one pad. Do not build on either.

Project 5's `scenes` file also changed between the two backups with no write
from us. The device rewrites the active project on its own; treat any
byte-level expectation about the active project as unstable while the device is
running.

### Consequences

- The "Restore, demonstrated" section of
  [backup-verification.md](backup-verification.md) is downgraded: restore
  reverts *most* state, and we have one confirmed case where it did not revert
  the active project's pad record. It is **not yet a proven safety net** for
  destructive operations on the active project.
- Restore verification must be by backup diff, never by live read.
- Until this is understood, tools that modify the active project should
  recommend the user switch projects first, and every restore should be
  followed by a fresh backup and a diff before the device is considered clean.

## Device state left behind

- Slots 15 and 16 both hold the test tone; slot 15 is redundant.
- Project 5 node 7207 (pad "1") assigned to slot 16 — our proof.
- Project 5 node 7204 (pad "4") reads slot 14 — the drift above.
- Project 6 nodes 8204 and 8207 armed by the stale-reference effect.

None of this is destructive, but the device is not in its backed-up state.
Cleaning up means clearing those pads and removing slots 15 and 16 — and we have
not mapped any library delete path yet.
