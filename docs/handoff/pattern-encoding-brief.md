# Handoff — pattern and scene encoding feasibility (dex `pblk0v4j`)

For a second agent working in a separate worktree, in parallel with Phase 1
hardware work. Self-contained: assume no shared context.

## The job

Decide whether we can **read and generate EP-133 pattern and scene data reliably
enough to ship `generate_ppak`**, and write down the encoding with per-field
verification status.

This is a **feasibility gate, not an implementation task.** "We cannot do this
safely, and here is the evidence" is a perfectly good outcome and should be
recorded as such rather than worked around.

Deliverable: `docs/research/pattern-encoding.md`, plus any decoder tooling under
`tools/`, prefixed `pattern_` to avoid collisions.

## Hard constraints

1. **Never touch the device.** No `mido`, no `python-rtmidi`, no opening a MIDI
   port, and do not run any existing script in `tools/` that does — most of them
   do. Another agent owns the hardware, and two processes on the same port
   corrupt each other's request/response matching. Everything here is offline
   file analysis.
2. **Work from a copy** of `~/Documents/ep133-backups/EP-133_*_backup.pak`. Treat
   it as read-only and irreplaceable: it is the only full backup of a real user's
   device, and the restore path is not yet fully trusted.
3. **Do not edit** anything under `docs/research/` except your own new file, and
   do not edit `docs/upstream/`, existing `tools/*.py`, or `.dex/`. Another agent
   is actively amending those.
4. **Never commit the `.pak`** or any of the user's sample audio. `*.pak` is
   gitignored. Sanitize anything you extract: the device serial appears inside
   GREET frames, and sample names are the user's private content. Pattern bytes
   and slot ids are fine.

## Established facts you can build on

These are verified on hardware; do not re-derive them.

- **Pad numbering is one index, everywhere.** Metadata node suffix, SysEx
  `pad_num`, and the project TAR's `pNN` all use the same visual top-to-bottom,
  left-to-right index: 1="7", 4="4", 7="1", 10=".". `PROTOCOL.md` §3.1 claims the
  TAR is bottom-up and supplies a translation table — **that table is wrong**, and
  we have a patch drafted against it. Do not apply it.
- **Project node base = `2000 + N × 1000`.** P05.tar is project 5 at base 7000.
- **Pad record**: slot is u16 LE at offsets 1–2 (not the u8 `PROTOCOL.md` §7
  documents); a pad is assigned when the u32 LE at offsets 8–11 is non-zero.
- **A pad reads as empty whenever its stored slot no longer exists.** The stored
  slot id survives in the record. This device has 36 dangling ids across 63 pads.
- Details and evidence: [`../research/phase0-proof.md`](../research/phase0-proof.md),
  [`../research/backup-verification.md`](../research/backup-verification.md),
  [`../research/protocol-audit.md`](../research/protocol-audit.md).

## What is actually in a project TAR

`PROTOCOL.md` §8 lists only `pads/` and an empty `patterns/` directory, and
states there is **no `settings` file** — "adding one in our generator triggered
ERROR CLOCK 43". Real backups from this device disagree. Every project contains:

```
pads/{a,b,c,d}/p01..p12   48 pad records (26 or 27 bytes — both forms appear)
patterns/{a,b,c,d}NN      4 to 708 bytes each, 400+ across the 9 projects
scenes                    712 bytes (612 in project 6)
settings                  222 bytes (220 in projects 6–9)
fx_settings               160 bytes (144 in projects 6–9)
```

So §8's structure section is incomplete and its `settings` claim is at best
version-specific. Whatever broke their generator, it was not the mere presence of
the file. **Resolving that is directly on the critical path**, because it is the
documented reason not to emit these files, and it is apparently wrong.

## The strongest lead

**Every pattern file size is ≡ 4 (mod 8)** — 4, 12, 20, 28, 36, 44, 52, 60, 68,
76, … up to 708. That is a **4-byte header plus N × 8-byte events**, and it
matches `ep133/song/format.py` in the pinned upstream checkout, whose
`build_pattern` emits a 4-byte header and 8-byte events laid out as:

```
position_ticks u16 LE | (pad-1)*8 | note | velocity | duration_ticks u16 LE | 0x00
```

A 4-byte file is an empty pattern. This is a very good sign: the format is
probably already understood on the write side.

**The real value here is that we have ground truth and upstream does not.**
Upstream's builders are write-side and validated mostly against their own
round-trips plus one minimal reference. We hold 400+ patterns produced by a real
device across nine real projects. Nobody has checked upstream's encoder against
that.

So the highest-value work is: **decode our patterns with upstream's stated layout
and find where it disagrees.** Concrete starting samples from P05:

```
patterns/a01 (20B): 00040200 f605003c64ff0100 f805003c64100010
patterns/a02 (44B): 00030500 3b02403c643b0008 2703503c640a0000 ...
patterns/b01  (4B): 00000000   <- empty
scenes  (712B): 000101010104040107010104040000000004040000...
```

Note `a01`'s header `00 04 02 00` against `a02`'s `00 03 05 00` — header bytes
vary per pattern and are worth attention early (bar count? event count? length?).

## Questions to answer, in priority order

1. Does upstream's event layout decode our real patterns into musically sensible
   results — plausible positions, pads in 1..12, notes in range, durations that
   do not overrun the bar?
2. What are the 4 header bytes? Test any theory across all 400+ patterns, not one.
3. Pattern slots exceed 12 per group (P03 has `a20`, P09 has `c17`). How many
   exist, and what does the numbering mean?
4. What is in `scenes`, and why is project 6's 612 bytes when every other is 712?
   The brief treats scene storage as unmapped and song mode as out of scope — if
   scenes turn out to be tractable, say so, but **do not quietly expand scope**;
   song mode being a non-goal is a product decision, not a technical one.
5. Can we identify BPM and time signature in `settings`? Upstream reports BPM as
   float32; `settings` begins `0000000000000743`, and `0x43070000` is 135.0 as
   float32 LE — worth checking against what the device actually shows for that
   project, though confirming it needs the hardware agent.
6. Given all that: can we generate a `.ppak` we would be willing to load onto a
   user's device? What would have to be true first?

## Standard of evidence

Match the rest of this repo, which has been bitten twice by confident wrong
claims:

- Mark every field **verified / probable / guessed**, and say what would falsify it.
- Test a theory against all nine projects before believing it. Several early
  findings here survived one project and died on the full set.
- Where you disagree with upstream, say so explicitly and write it up for
  reporting — `docs/upstream/` already has drafts in that style. Do not silently
  fork.
- **Importing a generated `.ppak` is not your call.** A bad project file has
  already cost one contributor a SHIFT+ERASE flash format. Hand any candidate to
  the hardware agent with an explicit risk note.
