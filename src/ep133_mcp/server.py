"""MCP server over stdio.

Stdout is the MCP stream. Nothing in this package writes to it directly; all
logging is configured to stderr before anything else runs.

Writes require current backups and complete preflight (docs/design/tool-contracts.md).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import sys
import threading
from typing import Any

from mcp.server.mcpserver import MCPServer

from . import __version__
from .device import DeviceError, DeviceSession, DeviceUnavailable
from .safety.backup import BackupRegistry, RESTORE_PROCEDURE
from .safety.install import Installer
from .safety.journal import Journal

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("ep133_mcp")

server = MCPServer(
    name="ep133-mcp",
    version=__version__,
    instructions=(
        "Installs samples onto a Teenage Engineering EP-133 K.O. II. "
        "Read-only tools never modify the device. Writes require a verified current backup. "
        "Show needs_confirmation impact to the owner and obtain approval before echoing its token. "
        "Kit installs are sequential and may partially succeed."
    ),
)

_session: DeviceSession | None = None
_session_lock = threading.Lock()
_operation_lock = threading.RLock()
_backups = BackupRegistry(float(os.environ.get("EP133_BACKUP_MAX_AGE_SECONDS", "86400")))
_installer = Installer(_backups, Journal(os.environ.get(
    "EP133_JOURNAL_DIR", str(Path.home() / ".local/state/ep133-mcp/journal"))))


def _device() -> DeviceSession:
    """The process-wide session, opened on first use. One owner per port."""
    global _session
    with _session_lock:
        if _session is None:
            _session = DeviceSession().open()
        return _session


def _error(e: DeviceError) -> dict[str, Any]:
    return {"error": type(e).__name__, "message": str(e), **e.detail}


@server.tool(
    name="device_info",
    description=(
        "Identify the connected EP-133 and report memory. Read-only. "
        "Returns SKU, OS version, sample capacity and free bytes (re-read from the "
        "device on every call), the native sample rate, and the active project number. "
        "The device serial is omitted unless include_serial is true."
    ),
)
def device_info(include_serial: bool = False) -> dict[str, Any]:
    try:
        with _operation_lock:
            d = _device()
            g = d.greet()
            d.begin_read()
            root = d.sample_root()
            active = d.active_project()
    except DeviceUnavailable as e:
        return _error(e)
    except DeviceError as e:
        log.warning("device_info failed: %s", e)
        return _error(e)

    info: dict[str, Any] = {
        "product": g.product,
        "mode": g.mode,
        "sku": g.sku,
        "base_sku": g.base_sku,
        "os_version": g.os_version,
        "sw_version": g.sw_version,
        "bootloader_version": g.bl_version,
        "sample_capacity_bytes": root.max_capacity,
        "sample_free_bytes": root.free_space_in_bytes,
        "native_sample_rate_hz": root.native_rate,
        "active_project": active,
        "write_tools_available": True,
    }
    if include_serial:
        info["serial"] = g.serial
    return info


@server.tool(
    name="list_pads",
    description=(
        "Read all 48 pads in a project (1–9, default active). Returns resolved sym, "
        "stored slot and length, and stale_reference for nonzero stored slots "
        "absent from the library. Reads fresh project TAR and metadata."
    ),
)
def list_pads(project: int | None = None) -> dict[str, Any]:
    if project is not None and not 1 <= project <= 9:
        return {"error": "InvalidProject", "message": "project must be 1..9"}
    try:
        with _operation_lock:
            return _device().list_pads(project)
    except DeviceError as e:
        log.warning("list_pads failed: %s", e)
        return _error(e)


@server.tool(
    name="verify_backup",
    description=(
        "Validate a full Sample Tool .pak against device SKU/OS, library occupancy "
        "and all 432 stored pad slot/length fields. Read-only; returns current/stale "
        "and a SHA-256 backup_id. Does not compare audio content or prove full restore."
    ),
)
def verify_backup(path: str) -> dict[str, Any]:
    try:
        with _operation_lock:
            return _backups.verify(path, _device())
    except DeviceError as e:
        return _error(e)


@server.tool(
    name="restore_procedure",
    description="Return manual Sample Tool restore and post-restore backup comparison guidance. No device I/O.",
)
def restore_procedure() -> dict[str, Any]:
    return RESTORE_PROCEDURE


@server.tool(
    name="install_sample",
    description=(
        "Install one mono 16-bit 46875 Hz WAV onto a pad index (1..12). Requires "
        "a current verify_backup backup_id. Shows exact destructive impact for owner "
        "confirmation before writing. Verifies CRC and stored assignment, journals "
        "partial failures. Power-cycle persistence needs a separate hardware check."
    ),
)
def install_sample(path: str, project: int, group: str, pad: int,
                   backup_id: str, confirm: str | None = None) -> dict[str, Any]:
    return install_kit([{"pad": pad, "path": path}], project, group, backup_id, confirm)


@server.tool(
    name="install_kit",
    description=(
        "Install 1..12 distinct {pad, path} entries into a project/group. NOT a "
        "transaction: preflights all entries, then installs sequentially and stops "
        "on failure, reporting each outcome and a shared undo journal. Requires "
        "a verified current backup_id and owner approval for destructive impact."
    ),
)
def install_kit(mapping: list[dict[str, Any]], project: int, group: str,
                backup_id: str, confirm: str | None = None) -> dict[str, Any]:
    try:
        with _operation_lock:
            return _installer.install(mapping, project, group, backup_id, _device(), confirm)
    except DeviceError as e:
        return _error(e)


@server.tool(
    name="undo_last_install",
    description=(
        "Revert only assignments from the latest journal for this device, provided "
        "pads still match the installed values. Reports anything it cannot restore. "
        "Uploaded library slots remain in place; deletion is unverified."
    ),
)
def undo_last_install() -> dict[str, Any]:
    try:
        with _operation_lock:
            return _installer.undo(_device())
    except DeviceError as e:
        return _error(e)


@server.tool(
    name="server_status",
    description=(
        "Report this server's version and whether an EP-133 MIDI port is visible, "
        "without opening it. Safe to call when no device is attached."
    ),
)
def server_status() -> dict[str, Any]:
    import mido

    outs = mido.get_output_names()
    ins = mido.get_input_names()
    return {
        "version": __version__,
        "device_output_visible": any("EP-133" in n for n in outs),
        "device_input_visible": any("EP-133" in n for n in ins),
        "session_open": _session is not None,
        "write_tools_available": True,
    }


def main() -> None:
    log.info("ep133-mcp %s starting over stdio", __version__)
    try:
        server.run(transport="stdio")
    finally:
        if _session is not None:
            _session.close()


if __name__ == "__main__":
    main()
