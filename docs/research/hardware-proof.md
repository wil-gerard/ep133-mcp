# Phase 0 hardware proof

Prerequisite: [protocol audit](protocol-audit.md). No hardware write has been performed.

1. Connect and power the EP-133 over a data-capable USB cable. Establish one MIDI owner and verify input/output endpoints.
2. Read and record model, SKU, exact OS version, active project, sample inventory and pad assignments. Confirm total/free memory against Sample Tool; unresolved capacity blocks writes.
3. Create a full sounds-and-projects backup with Sample Tool. Preserve the original privately outside Git, compute SHA-256, verify archive readability and inventory, and document the matching restore procedure. A projects-only backup is insufficient.
4. Select an explicit test project, unoccupied library slot and group A physical pad label 1. Check both slot and pad occupancy: reusing a library slot can affect other projects. Any destructive change requires explicit confirmation of the exact affected data.
5. Capture a known-good upload and assignment, then compare with pinned upstream fixtures. Use only a short user-owned or clearly licensed WAV. Retain sanitized traces without serial numbers or private sample contents in Git.
6. Build the bare Python proof only after the confirmed frames and recovery procedure exist. Back up before its write operation; validate all input and refuse unverified fields. No firmware operations or speculative raw writes.
7. Verify sample bytes/metadata and assignment after upload. Have the user power-cycle the device and verify the same physical pad still plays the sample; save evidence separately from assumptions.
8. Demonstrate the documented recovery path on explicitly authorized test data, retaining a full backup before the restore write. Record exact result and limitations.

Completion requires all of the above. If upload succeeds but pad assignment, persistence or recovery fails, record the failure in dex and leave downstream MCP implementation blocked.
