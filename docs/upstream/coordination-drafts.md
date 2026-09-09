# Upstream coordination drafts

Drafts only; nothing posted. Verify open issues for duplicates immediately before posting. These describe a private project without linking or exposing the private repository.

## ZacharySBrown/ep133-ppak

Title: Clarify verified upload/assignment and project-format evidence for an MCP integration

I am investigating a Python MCP interface for sample installation on EP-133, with full backup before writes and no firmware operations. At revision 93f0f6b1f6110701e6378e2702f39fd7e9a9d687, `EP133Client.assign_pad` implements live assignment and `ep133/song` provides pattern/scene builders, while PROTOCOL.md still lists project writing and pattern/scene storage as unresolved.

The 49 packing, upload-payload and assignment tests pass locally. One specific discrepancy: `upload_sample` says FILE_INFO commits the upload, but its generated sequence ends at the terminator, and the full-reproduction test excludes FILE_INFO. Is FILE_INFO necessary for persistence, or is that docstring outdated?

Which protocol sections should an integration treat as current, and which hardware/OS combinations verify upload plus metadata assignment surviving a power cycle? Is there a sanitized capture for that full sequence and a confirmed total/free-memory query? I also see optional factory/full-song fixtures: is there a small distributable fixture that independently establishes the 26-byte pad format and pattern import behavior? I have not yet validated this on my hardware and do not want to infer support from round-trip tests alone.

## ZacharySBrown/ep133-ppak — second issue (hardware-verified, ready to file)

Title: PROTOCOL.md §3 TAR↔SysEx pad translation table appears to be wrong

Drafted 2026-09-09 from hardware evidence. See
[backup verification](../research/backup-verification.md) for the full data.

PROTOCOL.md §3 states the project TAR is numbered bottom-up (`pads/c/p01` is the
bottom-left pad, label ".") and gives a translation table mapping TAR `p01` to
SysEx `pad_num` 10, `p04` to 7, and so on. On my unit that translation does not
hold: the TAR uses the *same* numbering as `pad_num`.

Evidence, on an EP-133 SKU TE032AS001, OS 2.5.1:

1. Reading `sym` from all 432 pad metadata nodes and comparing against the slot
   ids decoded from the project TARs of a Sample Tool backup taken minutes
   earlier: node suffix `NN` matched TAR `pNN` with 369 exact agreements, 63
   explained by slots deleted from the library, and 0 unexplained. Applying the
   §3 row-reversal instead left 118 pads unexplained.
2. Two single-pad `FILE_METADATA_SET {"sym":14}` writes: node 7204 loaded the pad
   physically labelled "4", node 7207 loaded the pad labelled "1". Both agree
   with the §3 `pad_num` table, so `pad_file_id()` and `assign_pad()` are right.

Together those give TAR `p04` = pad_num 4 = physical "4", where §3's table says
TAR `p04` = pad_num 7 = physical "1". If that is right, code applying the
documented translation while patching a `.ppak` places every pad in the wrong
row — which is exactly the failure §3 warns about, in the opposite direction.

Two caveats I cannot resolve alone: this is one unit on one OS, and I verified
*reading* a Sample Tool export, not what the device does when *importing* a
hand-built `.ppak`. It is possible export and import differ, or that the table
describes an older Sample Tool version. Happy to run specific checks.

Separately, and lower confidence: §7 lists pad-record offset 1 as `slot u8` with
offset 2 as `midiChannel`. My library has slots up to 935, and reading offsets
1–2 as u16 LE resolves every occupied pad to a slot the device confirms, so
offset 2 looks like the slot's high byte on this firmware.

## garrettjwilke/ep_133_sample_tool (check maintained fork first)

Title: Confirm current capture and full-backup workflow for a sample-install integration

I am investigating a Python MCP interface for EP-133 sample installation, with full backup before writes and no firmware operations. Your README marks this wrapper unmaintained, points to pbarilla/ep_133_sample_tool, and links ep_133_sysex_thingy for standalone transfers.

Is the maintained fork the right place for capture/compatibility questions? I need a reproducible capture of upload plus pad assignment, a full sounds-and-projects backup/restore baseline, and a confirmed device capacity/free-space response. Are there existing traces or protocol notes for those operations and their tested OS versions? I have not yet run a hardware proof; I am trying to reuse established evidence before writing another transfer implementation.
