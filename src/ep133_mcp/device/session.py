"""DeviceSession: the one owner of the EP-133 MIDI port in this process.

Design rules (docs/design/tool-contracts.md):

- Exactly one session per process. Opening it claims both endpoints named
  "EP-133"; if either is missing or busy, `DeviceUnavailable` says which.
- Nothing is cached. The device rewrites its own state while running, so every
  method is a fresh round trip.
- Every read starts with GREET and a read-mode FILE_INIT, mirroring the captured
  working sequences instead of assuming mode persists across calls.
- Metadata reads walk pages to the null terminator. Existence is decided by the
  response status, never by whether the JSON parsed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import logging
import zlib
import queue
import struct
import threading
import time
import tarfile
from dataclasses import dataclass

from ..protocol import payloads as P
from ..protocol import projects
from ..protocol.sysex import CMD_FILE, CMD_GREET, RequestIds, Response, build_frame, parse_frame

log = logging.getLogger(__name__)

PORT_NAME = "EP-133"
MAX_METADATA_PAGES = 16


class DeviceError(Exception):
    """Base for device-side failures. Carries structured detail for the MCP layer."""

    def __init__(self, message: str, **detail):
        super().__init__(message)
        self.detail = detail


class DeviceUnavailable(DeviceError):
    pass


class DeviceTimeout(DeviceError):
    pass


class DeviceRejected(DeviceError):
    """The device answered with a non-zero status."""


@dataclass(frozen=True)
class SampleRoot:
    max_capacity: int
    free_space_in_bytes: int
    native_rate: int


class DeviceSession:
    def __init__(self, inter_message_delay_s: float = 0.01, timeout_s: float = 5.0):
        self._delay = inter_message_delay_s
        self._timeout = timeout_s
        self._ids = RequestIds()
        self._identity = 0
        self._responses: queue.Queue[Response] = queue.Queue()
        self._lock = threading.Lock()
        self._out = None
        self._in = None
        self._ownership = None

    # ---- lifecycle -------------------------------------------------------

    def open(self) -> "DeviceSession":
        import mido

        outs = [n for n in mido.get_output_names() if PORT_NAME in n]
        ins = [n for n in mido.get_input_names() if PORT_NAME in n]
        if len(outs) != 1 or len(ins) != 1:
            raise DeviceUnavailable(
                "Expected exactly one EP-133 MIDI input and output",
                outputs=mido.get_output_names(),
                inputs=mido.get_input_names(),
                next_step="Connect and power the EP-133 over a data-capable USB cable, "
                          "and close EP Sample Tool or any other program holding the port.",
            )
        try:
            import fcntl
            directory = Path.home() / '.local/state/ep133-mcp'
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._ownership = (directory / 'device.lock').open('a')
            fcntl.flock(self._ownership.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._out = mido.open_output(outs[0])
            self._in = mido.open_input(ins[0], callback=self._on_message)
        except (OSError, ImportError) as e:
            self.close()
            raise DeviceUnavailable(
                f"could not open EP-133 port: {e}",
                next_step="Another process may own the port. Close it and retry.",
            ) from e
        log.info("opened %s / %s", outs[0], ins[0])
        return self

    def close(self) -> None:
        for port in (self._in, self._out):
            if port is not None:
                port.close()
        self._in = self._out = None
        if self._ownership is not None:
            self._ownership.close()
            self._ownership = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()

    # ---- transport -------------------------------------------------------

    def _on_message(self, msg) -> None:
        if msg.type != "sysex":
            return
        parsed = parse_frame(bytes([0xF0, *msg.data, 0xF7]))
        if parsed is not None:
            self._responses.put(parsed)

    def request(self, command: int, payload: bytes) -> Response:
        """Send one frame and block for its matching response."""
        if self._out is None:
            raise DeviceUnavailable("session not open")
        with self._lock:
            rid = self._ids.next()
            frame = build_frame(command, payload, rid, identity=self._identity)
            import mido
            self._out.send(mido.Message("sysex", data=frame[1:-1]))
            time.sleep(self._delay)
            deadline = time.monotonic() + self._timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DeviceTimeout(
                        f"no response to command 0x{command:02x} (request {rid})",
                        command=command, request_id=rid,
                    )
                try:
                    r = self._responses.get(timeout=remaining)
                except queue.Empty:
                    continue
                if r.request_id == rid and r.command == command:
                    return r
                log.debug("discarding unmatched response id=%s", r.request_id)

    # ---- channel messages ------------------------------------------------

    def send_note(self, channel: int, note: int, velocity: int = 100, duration_s: float = 0.25) -> dict:
        """A MIDI note on the device's own port, held for duration_s, then released.

        Not SysEx: the EP-133 takes external MIDI notes on the channels its groups are set to, so
        this needs no protocol work and cannot leave a file open. What it can do is land in a
        pattern if the device is recording, and which note reaches which pad is the device's MIDI
        setting, not ours - docs/research/play-proof.md records what was observed."""
        if type(channel) is not int or not 1 <= channel <= 16:
            raise ValueError("channel must be 1..16")
        if type(note) is not int or not 0 <= note <= 127:
            raise ValueError("note must be 0..127")
        if type(velocity) is not int or not 1 <= velocity <= 127:
            raise ValueError("velocity must be 1..127")
        if isinstance(duration_s, bool) or not isinstance(duration_s, (int, float)) or not 0.01 <= duration_s <= 5.0:
            raise ValueError("duration_s must be 0.01..5")
        if self._out is None:
            raise DeviceUnavailable("session not open")
        import mido
        with self._lock:
            started = time.monotonic()
            self._out.send(mido.Message("note_on", channel=channel - 1, note=note, velocity=velocity))
            time.sleep(duration_s)
            self._out.send(mido.Message("note_off", channel=channel - 1, note=note, velocity=0))
            held = time.monotonic() - started
        return {"channel": channel, "note": note, "velocity": velocity, "held_s": round(held, 3)}

    # ---- verified read operations ---------------------------------------

    def greet(self) -> P.Greeting:
        r = self.request(CMD_GREET, b"")
        if not r.ok:
            raise DeviceRejected("GREET rejected", status=r.status)
        self._identity = r.identity
        return P.Greeting.parse(r.payload)

    def begin_read(self) -> None:
        r = self.request(CMD_FILE, P.file_init(P.READ_MODE))
        if not r.ok:
            raise DeviceRejected("read-mode FILE_INIT rejected", status=r.status)

    def metadata(self, file_id: int) -> dict | None:
        """Metadata for a file id, or None if the device says it does not exist.

        Existence comes from the status of page 0. A record that exists but does
        not parse is returned as {"_unparsed": <bytes>} — it is still occupied.
        """
        body = b""
        for page in range(MAX_METADATA_PAGES):
            r = self.request(CMD_FILE, P.metadata_get(file_id, page))
            if not r.ok:
                if page == 0 and r.status == 1 and r.payload.rstrip(b'\0').lower() == b'invalid file id':
                    return None
                raise DeviceRejected('metadata read rejected', file_id=file_id, page=page, status=r.status)
            chunk = P.parse_metadata_page(r.payload)
            if not chunk:
                return {'_unparsed': len(body)}
            body += chunk
            end = body.find(b"\0")
            if end >= 0:
                body = body[:end]
                break
        else:
            return {'_unparsed': len(body)}
        try:
            parsed = json.loads(body.decode("utf-8"))
            return parsed if isinstance(parsed, dict) else {'_unparsed': len(body)}
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {"_unparsed": len(body)}

    def stat(self, file_id: int) -> dict | None:
        """STAT one id: its node, parent, flags, size and name, or None when the device says invalid.

        Any other error status is raised, not swallowed: an unknown id is exactly where the
        interface has surprised us before."""
        r = self.request(CMD_FILE, P.file_info(file_id))
        if not r.ok:
            text = r.payload.rstrip(b"\0").decode("latin-1", "replace").lower()
            if r.status == 1 and text in ("invalid file id", "invalid id"):
                return None
            raise DeviceRejected("stat rejected", file_id=file_id, status=r.status, reason=text)
        try:
            return P.parse_file_info(r.payload)
        except ValueError as e:
            raise DeviceRejected(str(e), file_id=file_id, payload=r.payload.hex()) from e

    def list_directory(self, node: int, max_pages: int = 64) -> list[dict]:
        """Every child of a directory node, paging FILE_LIST until an empty page."""
        entries = []
        for page in range(max_pages):
            r = self.request(CMD_FILE, P.file_list(node, page))
            if not r.ok:
                raise DeviceRejected("file list rejected", node=node, page=page, status=r.status,
                                     reason=r.payload.rstrip(b"\0").decode("latin-1", "replace"))
            try:
                chunk = P.parse_file_list(r.payload, page)
            except ValueError as e:
                raise DeviceRejected(str(e), node=node, page=page) from e
            if not chunk:
                return entries
            entries.extend(chunk)
        raise DeviceRejected("file list page limit reached", node=node)

    def walk(self, node: int = 0, path: str = "/", max_depth: int = 6, max_nodes: int = 4096,
             skip: set[int] = frozenset()) -> list[dict]:
        """Depth-first listing from a node, each entry with its path. skip holds folder nodes not
        to descend into (the 999-slot sounds root, say)."""
        out = []
        stack = [(node, path, 0)]
        while stack:
            current, prefix, depth = stack.pop()
            for entry in self.list_directory(current):
                full = prefix.rstrip("/") + "/" + entry["name"]
                out.append({**entry, "path": full, "parent": current, "depth": depth})
                if len(out) >= max_nodes:
                    raise DeviceRejected("walk node limit reached", nodes=len(out))
                if entry["kind"] == "folder" and depth + 1 < max_depth and entry["node"] not in skip:
                    stack.append((entry["node"], full, depth + 1))
        return out

    def sample_root(self) -> SampleRoot:
        m = self.metadata(P.SAMPLE_ROOT)
        if not m or "max_capacity" not in m:
            raise DeviceRejected("sample root metadata unavailable", observed=m)
        native = 0
        for fmt in m.get("formats", []):
            for f in fmt.get("formats", []):
                native = f.get("samplerate.native", native)
        return SampleRoot(int(m["max_capacity"]), int(m["free_space_in_bytes"]), int(native))

    def active_project(self) -> int:
        m = self.metadata(P.PROJECT_ROOT)
        if not m or "active" not in m:
            raise DeviceRejected("project root metadata unavailable", observed=m)
        return P.project_base(int(m["active"]))

    def pad_metadata(self, project: int, group: str, pad_num: int) -> dict:
        """The whole JSON record the device holds for a pad node.

        `sym` is the resolved slot (0 when unassigned or stale); the other keys are the per-pad
        sound parameters upstream PROTOCOL.md section 6 lists. Which keys this OS actually returns is
        recorded in docs/research/pad-metadata.md as they are observed."""
        m = self.metadata(P.pad_node(project, group, pad_num))
        if m is None or "sym" not in m:
            raise DeviceRejected("pad metadata unavailable", observed=m)
        return m

    def pad_sym(self, project: int, group: str, pad_num: int) -> int:
        """Resolved slot for a pad; 0 means the device considers it unassigned.

        A stale stored slot also reads 0 here. Only a project TAR read shows
        the stored field.
        """
        return int(self.pad_metadata(project, group, pad_num)["sym"] or 0)

    def read_pad(self, project: int, group: str, pad_num: int) -> dict:
        """One pad's full JSON plus, when it resolves to a slot, that slot's full JSON."""
        node = P.pad_node(project, group, pad_num)  # validate before any I/O
        self.greet()
        self.begin_read()
        pad = self.pad_metadata(project, group, pad_num)
        sym = int(pad["sym"] or 0)
        slot = self.metadata(sym) if sym else None
        return {"project": project, "group": group, "pad": pad_num, "label": P.PAD_LABELS[pad_num],
                "node": node, "sym": sym, "pad_metadata": pad, "slot_metadata": slot}


    def project_tar(self, project: int) -> bytes:
        payload = projects.project_open(project)  # validate before any I/O
        self.greet()
        self.begin_read()
        r = self.request(CMD_FILE, payload)
        if not r.ok:
            raise DeviceRejected("project open rejected", project=project, status=r.status)
        chunks = []
        for page in range(projects.MAX_PROJECT_PAGES):
            r = self.request(CMD_FILE, projects.project_page(page))
            if not r.ok:
                raise DeviceRejected("project page rejected", page=page, status=r.status)
            try:
                chunk = projects.page_data(r.payload, page)
            except ValueError as e:
                raise DeviceRejected(str(e), project=project, page=page) from e
            chunks.append(chunk)
            if len(chunk) < projects.PAGE_DATA_BYTES:
                return b"".join(chunks)
        raise DeviceRejected("project page limit reached without EOF", project=project)

    def read_file(self, file_id: int, max_pages: int = 200_000) -> bytes:
        """Stream a file's raw bytes with FILE_READ_OPEN / FILE_READ_DATA.

        The same pair that reads a project TAR also reads a sample slot: slot 17 came back as
        37,500 bytes of `017.pcm` whose crc32 equalled the slot metadata's crc, and byte for byte
        equal to the WAV payload Sample Tool put in its own backup. Roughly 25 KiB/s."""
        # A read left open and not drained to EOF wedges the interface: opening project 2 and
        # then project 3 without draining the first stopped the device answering anything,
        # GREET included, until a power cycle. Re-initialising read mode before each open keeps
        # one open read at a time even when a caller abandons one.
        self.begin_read()
        r = self.request(CMD_FILE, struct.pack(">BBHI", 3, 0, file_id, 0))
        if not r.ok:
            raise DeviceRejected("file read open rejected", file_id=file_id, status=r.status)
        chunks = []
        for page in range(max_pages):
            rr = self.request(CMD_FILE, projects.project_page(page) if page < projects.MAX_PROJECT_PAGES
                              else struct.pack(">BBH", 3, 1, page))
            if not rr.ok:
                raise DeviceRejected("file read page rejected", file_id=file_id, page=page, status=rr.status)
            try:
                chunk = projects.page_data(rr.payload, page)
            except ValueError as e:
                raise DeviceRejected(str(e), file_id=file_id, page=page) from e
            chunks.append(chunk)
            if len(chunk) < projects.PAGE_DATA_BYTES:
                return b"".join(chunks)
        raise DeviceRejected("file page limit reached without EOF", file_id=file_id)

    def slot_pcm(self, slot: int) -> bytes:
        """One library slot's raw PCM, checked against the crc the device stores for it."""
        meta = self.metadata(slot)
        if not meta:
            raise DeviceRejected("slot is empty", slot=slot)
        data = self.read_file(slot)
        if zlib.crc32(data) != meta.get("crc"):
            raise DeviceRejected("slot read does not match its stored crc", slot=slot,
                                 observed=zlib.crc32(data), expected=meta.get("crc"))
        return data

    def slot_exists(self, slot: int) -> bool:
        r = self.request(CMD_FILE, P.metadata_get(slot))
        if r.ok:
            return True
        if r.status == 1 and r.payload.rstrip(b"\0").lower() == b"invalid file id":
            return False
        raise DeviceRejected("slot existence unknown", slot=slot, status=r.status)

    def list_pads(self, project: int | None = None, fields: bool = False) -> dict:
        """All 48 pads with stored slot/length, resolved sym and, with fields, the whole pad JSON."""
        if project is not None:
            projects.project_open(project)
        else:
            self.greet()
            self.begin_read()
            project = self.active_project()
        try:
            pads = projects.stored_pads(self.project_tar(project))
        except (ValueError, tarfile.TarError) as e:
            raise DeviceRejected("invalid project TAR", project=project, reason=str(e)) from e
        self.begin_read()
        # Only referenced slots need existence checks. This is local to this
        # call, never cached across calls or inferred from resolved sym.
        exists = {slot: self.slot_exists(slot) for slot in {p["stored_slot"] for p in pads}}
        for pad in pads:
            metadata = self.pad_metadata(project, pad["group"], pad["pad"])
            pad["sym"] = int(metadata["sym"] or 0)
            pad["node"] = P.pad_node(project, pad["group"], pad["pad"])
            if fields:
                pad["metadata"] = metadata
            pad["stale_reference"] = pad["stored_slot"] != 0 and not exists[pad["stored_slot"]]
        return {"project": project, "pads": pads}

    # ---- captured write operations --------------------------------------

    def _write_request(self, payload: bytes):
        response = self.request(CMD_FILE, payload)
        if not response.ok:
            raise DeviceRejected('device write rejected', status=response.status)

    def begin_write(self):
        self.greet()
        self._write_request(P.file_init(P.WRITE_MODE))

    def upload_sample(self, slot: int, name: str, pcm: bytes):
        meta = P.file_put_meta(name, len(pcm), slot)  # validate before any I/O
        self.begin_write()
        self._write_request(meta)
        count = (len(pcm) + P.UPLOAD_CHUNK_BYTES - 1) // P.UPLOAD_CHUNK_BYTES
        for page in range(count):
            offset = page * P.UPLOAD_CHUNK_BYTES
            self._write_request(P.file_put_data(page, pcm[offset:offset + P.UPLOAD_CHUNK_BYTES]))
        self._write_request(P.file_put_data(count, b''))

    def delete_slot(self, slot: int) -> bool:
        """Delete one library slot and report whether it is gone afterwards.

        FILE_DELETE is documented upstream and UNVERIFIED here, so the return value comes from
        re-reading the slot rather than from the command's own status: a device that ignores the
        command answers ok and changes nothing."""
        self.begin_write()
        response = self.request(CMD_FILE, P.file_delete(slot))
        if not response.ok:
            raise DeviceRejected('device rejected the delete', slot=slot, status=response.status,
                                 reason=response.payload.rstrip(b'\0').decode('latin-1'))
        self.begin_read()
        return not self.slot_exists(slot)

    def set_metadata(self, file_id: int, fields: dict):
        """Partial-merge JSON write to a pad node or slot: only the given keys change (upstream 10.3).

        The same call that assigns a pad ({'sym': slot}, proven here) carries the per-pad sound
        parameters upstream verified; a status-0 answer is not proof the device kept a field, so
        every caller reads the record back afterwards."""
        payload = P.metadata_set(file_id, fields)  # validate before any I/O
        self.begin_write()
        self._write_request(payload)

    def assign_pad(self, node: int, slot: int):
        self.set_metadata(node, {'sym': slot})
