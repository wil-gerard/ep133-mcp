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

## Unresolved: node 7204 drifted back after a verified restore

Node 7204 was set to slot 14 during the pad-addressing tests. The restore
reverted it — verified explicitly, `7204=0`, 0 unexplained across 432 pads. With
no further write to that node, it later read 14 again, and it still reads 14
after the power cycle, so whatever happened reached flash.

The backup stores slot field 341 at `P05/a/p04`, so 14 did not come from the
restored record.

**Refined hypothesis, 2026-09-10 — the restore was probably still in flight when
we verified it.** A pad reads `sym` = 0 whenever its stored slot field points at
a slot that does not exist. If Sample Tool restores projects and sounds in
separate phases, then during the window where project records were written but
the sound library was not yet complete, a pad holding slot 14 would read 0 simply
because slot 14 did not exist *yet*. Our verification ran immediately after the
owner said the restore had finished, which is exactly that window.

That would mean the restore never reverted node 7204's stored field at all — it
still holds the 14 we wrote — and the `7204=0` PASS was an artifact of reading
mid-restore rather than evidence of reversion.

This fits better than the earlier RAM-flush guess, which could not explain why
7204 drifted while 7207, written and restored identically, did not. Under the
in-flight reading, 7207 differs because we later overwrote it with slot 16
deliberately, so its stored field was never expected to match the backup.

Neither hypothesis is confirmed.

If that is right, then **restoring a backup does not reliably revert the active
project**, and the restore result in
[backup-verification.md](backup-verification.md) is weaker than stated there —
it was verified immediately after the restore, which is exactly when this
hypothesis says the reading would still look correct. Recovery may require a
power cycle before verification, or restoring while a different project is
active.

### How to settle it

The device does not expose stored pad-record bytes — `FILE_METADATA_GET` returns
the resolved `sym`, so a stale reference and a genuinely empty pad are
indistinguishable live. The stored field is only visible in a backup.

So: take a **fresh backup now** and diff its `P05/a/p04` record against the
2026-09-09 backup with [`tools/diff_backups.py`](../../tools/diff_backups.py).

- Stored slot **341** → the restore did revert the record, and something later
  re-wrote 14. The RAM-flush hypothesis survives.
- Stored slot **14** → the restore never reverted this pad, and our verification
  passed because it ran before the sound library finished restoring. Restore is
  not atomic, and verifying one immediately after it reports "done" is unsound.

The second outcome is the more serious one: it would mean our restore
verification method is wrong, not just that one pad drifted. Any future restore
check must power-cycle first, or at minimum re-verify after a delay.

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
