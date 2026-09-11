# Upstream coordination drafts

Drafts only; nothing posted. Verify open issues for duplicates immediately before posting. These describe a private project without linking or exposing the private repository.

## ZacharySBrown/ep133-ppak

Title: Clarify verified upload/assignment and project-format evidence for an MCP integration

I am investigating a Python MCP interface for sample installation on EP-133, with full backup before writes and no firmware operations. At revision 93f0f6b1f6110701e6378e2702f39fd7e9a9d687, `EP133Client.assign_pad` implements live assignment and `ep133/song` provides pattern/scene builders, while PROTOCOL.md still lists project writing and pattern/scene storage as unresolved.

The 49 packing, upload-payload and assignment tests pass locally. One specific discrepancy: `upload_sample` says FILE_INFO commits the upload, but its generated sequence ends at the terminator, and the full-reproduction test excludes FILE_INFO. Is FILE_INFO necessary for persistence, or is that docstring outdated?

Which protocol sections should an integration treat as current, and which hardware/OS combinations verify upload plus metadata assignment surviving a power cycle? Is there a sanitized capture for that full sequence and a confirmed total/free-memory query? I also see optional factory/full-song fixtures: is there a small distributable fixture that independently establishes the 26-byte pad format and pattern import behavior? I have not yet validated this on my hardware and do not want to infer support from round-trip tests alone.

## ZacharySBrown/ep133-ppak — second issue (hardware-verified, ready to file)

Title: PROTOCOL.md §3.1 project-TAR pad numbering contradicts the library's own
song builder

Drafted 2026-09-09, re-audited 2026-09-10. See
[backup verification](../research/backup-verification.md) for the data.

**This looks like a documentation bug, not a code bug.** `pad_file_id()`,
`assign_pad()` and the `ep133/song` builders all appear correct; §3.1 is the
part that does not match.

§3.1 says the project TAR is numbered bottom-up — `p01` = "." (bottom-left),
`p04` = "1" — and gives a translation table mapping TAR `pNN` to a *different*
SysEx `pad_num`. On my unit the TAR uses the **same** numbering as `pad_num`,
so the translation is an identity and the table is a row-reversal that should
not be applied.

Three independent lines of evidence, on EP-133 SKU TE032AS001, OS 2.5.1:

1. **Your own song builder agrees with me.** `ep133/song/format.py` takes
   `PadSpec.pad` 1..12 and writes it straight to `pads/{group}/p{NN}` with no
   translation, and the comment at `build_pattern` states the reference has the
   "." pad stored at `pads/{group}/p10`. §3.1's table says "." is `p01`. Since
   "." is `pad_num` 10, `p10` = `pad_num` 10 — TAR numbering equals `pad_num`.
   `tests/test_song_format.py::test_build_pattern_pad_indicator_is_pad_minus_one_times_8`
   encodes the same reference.

2. **432-pad read comparison.** Reading `sym` from every pad metadata node and
   comparing against slot ids decoded from the project TARs of a Sample Tool
   backup taken minutes earlier: node suffix `NN` matched TAR `pNN` with 369
   exact agreements, 63 explained by slots deleted from the library, and 0
   unexplained. Applying §3.1's row-reversal instead left 118 unexplained.

3. **Two single-pad writes.** `FILE_METADATA_SET {"sym":14}` to node 7204 loaded
   the pad physically labelled "4"; to node 7207 it loaded the pad labelled "1".
   Both match §3's `pad_fid` formula and the `pad_num` table, which are correct.

What I have not established: this is one unit on one OS; and evidence (2) and (3)
verify *reading* device metadata and *reading* a Sample Tool export. Evidence (1)
is from your import-side builder, which is consistent, but I have not personally
round-tripped a hand-built `.ppak` through import to confirm the device agrees
there. It is also possible §3.1 documents an older Sample Tool version. Happy to
run specific checks on request.

Separately, and lower confidence: §7 lists pad-record offset 1 as `slot u8` with
offset 2 as `midiChannel`. My library has slots up to 935, and reading offsets
1–2 as u16 LE resolves every occupied pad to a slot the device confirms, so
offset 2 looks like the slot's high byte on this firmware. Note `PadSpec` already
documents `sample_slot` as "uint16 LE", which agrees.

## ZacharySBrown/ep133-ppak — third item (answers our own earlier question)

The first draft above asks whether FILE_INFO is required to commit an upload. We
have now answered it on hardware and should say so rather than ask: on
TE032AS001 / OS 2.5.1, the empty-data terminator commits. Probing the target slot
between the terminator and FILE_INFO, in one session, shows the slot already
exists with full, correct metadata and free space already decremented; the
subsequent FILE_INFO returns status 0 and changes nothing observable.

So `upload_sample` works and its docstring is wrong — worth a one-line fix so
nobody "repairs" a bug that is not there:

> `slot` is the target library slot (1-based). FILE_INFO commits the uploaded
> audio buffer to this slot — without it the device discards the upload.

Still unverified by us: whether an upload without FILE_INFO survives a power
cycle. Worth stating that limit in the report.

Also useful and possibly undocumented: the slot metadata `crc` field is a plain
CRC-32 of the raw PCM payload. We matched `zlib.crc32` exactly on a 37,500-byte
upload, which makes post-install verification possible without reading the sample
back. Happy to contribute that as a doc note if it is not already known.

## garrettjwilke/ep_133_sample_tool (check maintained fork first)

Title: Confirm current capture and full-backup workflow for a sample-install integration

I am investigating a Python MCP interface for EP-133 sample installation, with full backup before writes and no firmware operations. Your README marks this wrapper unmaintained, points to pbarilla/ep_133_sample_tool, and links ep_133_sysex_thingy for standalone transfers.

Is the maintained fork the right place for capture/compatibility questions? I need a reproducible capture of upload plus pad assignment, a full sounds-and-projects backup/restore baseline, and a confirmed device capacity/free-space response. Are there existing traces or protocol notes for those operations and their tested OS versions? I have not yet run a hardware proof; I am trying to reuse established evidence before writing another transfer implementation.
