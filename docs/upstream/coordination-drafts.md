# Upstream coordination drafts

Drafts only; nothing posted. Verify open issues for duplicates immediately before posting. These describe a private project without linking or exposing the private repository.

## ZacharySBrown/ep133-ppak

Title: Clarify verified upload/assignment and project-format evidence for an MCP integration

I am investigating a Python MCP interface for sample installation on EP-133, with full backup before writes and no firmware operations. At revision 93f0f6b1f6110701e6378e2702f39fd7e9a9d687, `EP133Client.assign_pad` implements live assignment and `ep133/song` provides pattern/scene builders, while PROTOCOL.md still lists project writing and pattern/scene storage as unresolved.

The 49 packing, upload-payload and assignment tests pass locally. One specific discrepancy: `upload_sample` says FILE_INFO commits the upload, but its generated sequence ends at the terminator, and the full-reproduction test excludes FILE_INFO. Is FILE_INFO necessary for persistence, or is that docstring outdated?

Which protocol sections should an integration treat as current, and which hardware/OS combinations verify upload plus metadata assignment surviving a power cycle? Is there a sanitized capture for that full sequence and a confirmed total/free-memory query? I also see optional factory/full-song fixtures: is there a small distributable fixture that independently establishes the 26-byte pad format and pattern import behavior? I have not yet validated this on my hardware and do not want to infer support from round-trip tests alone.

## garrettjwilke/ep_133_sample_tool (check maintained fork first)

Title: Confirm current capture and full-backup workflow for a sample-install integration

I am investigating a Python MCP interface for EP-133 sample installation, with full backup before writes and no firmware operations. Your README marks this wrapper unmaintained, points to pbarilla/ep_133_sample_tool, and links ep_133_sysex_thingy for standalone transfers.

Is the maintained fork the right place for capture/compatibility questions? I need a reproducible capture of upload plus pad assignment, a full sounds-and-projects backup/restore baseline, and a confirmed device capacity/free-space response. Are there existing traces or protocol notes for those operations and their tested OS versions? I have not yet run a hardware proof; I am trying to reuse established evidence before writing another transfer implementation.
