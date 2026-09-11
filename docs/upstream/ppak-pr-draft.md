# Draft PR — ZacharySBrown/ep133-ppak

Not submitted. Nothing has been pushed, forked or posted.

Patch: [`ppak-protocol-3.1.patch`](ppak-protocol-3.1.patch) — applies cleanly to
`PROTOCOL.md` at revision `93f0f6b1f6110701e6378e2702f39fd7e9a9d687`, verified
with `patch`. It touches only `PROTOCOL.md` §3.1 and adds a §3.2.

## Before submitting

1. **Rebase onto their current `main`.** The patch is against the revision we
   pinned on 2026-09-07. Our vendored clone has no newer commits fetched, so
   check whether §3.1 has already been corrected upstream — if it has, drop this
   entirely.
2. **Check open issues and PRs for duplicates.**
3. Decide whether to open an issue first and let the maintainer choose the
   wording, rather than arriving with a doc rewrite. For a single-maintainer RE
   project a PR that rewrites their documentation can read as presumptuous; the
   evidence is the valuable part, not our prose.
4. This is a public contribution under your GitHub identity. It describes a
   private project without linking it, and the sanitized evidence carries no
   serial number or sample audio.

## Proposed title

    docs: PROTOCOL.md 3.1 — project TAR uses the same pad numbering as SysEx

## Proposed description

PROTOCOL.md §3.1 describes two pad numbering conventions and supplies a
row-reversing translation table between the project TAR's `pads/{group}/p{NN}`
and the SysEx `pad_num`. I believe the TAR actually uses the *same* numbering as
`pad_num`, making the translation an identity.

The rest of §3 — the `pad_fid` formula and the `pad_num` grid — matches my
hardware exactly. Only the TAR convention block and its table look wrong.

Three lines of evidence, on EP-133 SKU TE032AS001, OS 2.5.1:

1. **This library already assumes the single convention.** `ep133/song/format.py`
   writes `PadSpec.pad` straight into `pads/{group}/p{NN}` with no translation,
   and the `build_pattern` comment cites a device reference where the "." pad is
   stored at `pads/{group}/p10`. "." is `pad_num` 10, so TAR numbering equals
   `pad_num` numbering. `test_build_pattern_pad_indicator_is_pad_minus_one_times_8`
   encodes the same reference. So §3.1 contradicts your own builder, and the
   builder matches my device.

2. **432-pad read comparison.** I read `sym` from every pad metadata node and
   compared it against slot ids decoded from the project TARs of a Sample Tool
   backup taken minutes earlier. Node suffix `NN` matched TAR `pNN` with 369
   exact agreements and 0 unexplained mismatches; 63 further pads are explained
   by library slots deleted after assignment, where the TAR keeps the stale slot
   id while the device reports the pad empty. Scoring §3.1's row-reversed mapping
   against the same snapshot left 118 pads unexplained.

3. **Two single-pad writes.** `FILE_METADATA_SET {"sym":14}` to pad fileId 7204
   loaded the pad physically labelled "4"; the same write to 7207 loaded the pad
   labelled "1". Both agree with §3's formula and the `pad_num` table.

### What I have not established

One unit, one OS. Items 2 and 3 verify *reading* device metadata and *reading* a
Sample Tool export; item 1 is import-side but I have not round-tripped a
hand-built `.ppak` through import myself. It is entirely possible §3.1 documents
an older Sample Tool version, in which case the right fix is a note about
versions rather than the rewrite I have drafted — happy to rework it that way,
or to run specific checks on my unit.

### Lower-confidence, not included in this patch

§7 lists pad-record offset 1 as `slot u8` with offset 2 as `midiChannel`. My
library has slots up to 935, and reading offsets 1–2 as u16 LE resolves every
occupied pad to a slot the device confirms, so offset 2 looks like the slot's
high byte on this firmware. `PadSpec.sample_slot` is already documented as
"uint16 LE", which agrees. Happy to open that separately with its own evidence.
