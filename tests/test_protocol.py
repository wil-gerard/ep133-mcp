"""Protocol layer against the hardware capture.

docs/research/fixtures/upload-capture-slot16.json holds every frame sent to and
received from a real EP-133 during a verified upload. The encoder must
reproduce each sent frame byte-for-byte from its unpacked payload, and the
parser must round-trip every response.
"""

import json
from pathlib import Path

import pytest

from ep133_mcp.protocol import payloads as P
from ep133_mcp.protocol.packing import pack, packed_length, unpack
from ep133_mcp.protocol.sysex import build_frame, parse_frame

CAPTURE = Path(__file__).resolve().parent.parent / "docs/research/fixtures/upload-capture-slot16.json"
FRAMES = json.loads(CAPTURE.read_text())["frames"]


@pytest.mark.parametrize("raw", [b"", b"\x00", b"\xff", bytes(range(7)), bytes(range(8)), bytes(range(256)) * 3])
def test_pack_unpack_roundtrip(raw):
    packed = pack(raw)
    assert len(packed) == packed_length(len(raw))
    assert all(b < 0x80 for b in packed)
    assert unpack(packed) == raw


@pytest.mark.parametrize("frame", FRAMES, ids=[f["label"] for f in FRAMES])
def test_sent_frames_reproduce_from_payload(frame):
    """Decode the captured frame's header + payload, re-encode, compare exactly."""
    sent = bytes.fromhex(frame["sent_hex"])
    identity = sent[4]
    request_id = ((sent[6] & 0x1F) << 7) | (sent[7] & 0x7F)
    command = sent[8]
    payload = unpack(sent[9:-1])
    assert build_frame(command, payload, request_id, identity=identity) == sent


@pytest.mark.parametrize("frame", FRAMES, ids=[f["label"] for f in FRAMES])
def test_captured_responses_parse(frame):
    """Rebuild the response frame from the capture and parse it."""
    sent = bytes.fromhex(frame["sent_hex"])
    request_id = ((sent[6] & 0x1F) << 7) | (sent[7] & 0x7F)
    # The capture stores the unpacked response payload; reconstruct the wire form.
    payload = bytes.fromhex(frame["response_hex"])
    wire = bytes([0xF0, 0x00, 0x20, 0x76, sent[4], 0x40, 0x20 | ((request_id >> 7) & 0x1F),
                  request_id & 0x7F, sent[8], frame["status"]]) + pack(payload) + bytes([0xF7])
    r = parse_frame(wire)
    assert r is not None
    assert r.request_id == request_id
    assert r.status == frame["status"]
    assert r.payload == payload


def test_greet_parses_and_serial_is_redacted_in_fixture():
    greet = next(f for f in FRAMES if f["label"] == "preflight/greet")
    g = P.Greeting.parse(bytes.fromhex(greet["response_hex"]))
    assert g.product == "EP-133"
    assert g.sku == "TE032AS001"
    assert g.os_version == "2.5.1"
    assert g.serial == "REDACTED", "fixture must never carry the real serial"


def test_read_mode_file_init_matches_capture():
    init = next(f for f in FRAMES if f["label"] == "preflight/file_init_read")
    assert unpack(bytes.fromhex(init["sent_hex"])[9:-1]) == P.file_init(P.READ_MODE)
    assert P.file_init(P.READ_MODE) == bytes.fromhex("010000400000")


def test_metadata_get_matches_capture():
    root = next(f for f in FRAMES if f["label"].startswith("preflight/sample_root"))
    assert unpack(bytes.fromhex(root["sent_hex"])[9:-1]) == P.metadata_get(P.SAMPLE_ROOT, 0)
    assert P.metadata_get(1000) == bytes.fromhex("070203e80000")


def test_metadata_set_layout():
    assert P.metadata_set(7207, {"sym": 16}) == bytes.fromhex("0701") + b"\x1c\x27" + b'{"sym":16}\x00'


def test_pad_node_matches_hardware_verified_addresses():
    # docs/research/phase0-proof.md: 7207 loaded physical "1", 7204 loaded physical "4"
    assert P.pad_node(5, "A", P.LABEL_TO_PAD["1"]) == 7207
    assert P.pad_node(5, "A", P.LABEL_TO_PAD["4"]) == 7204
    assert P.pad_node(1, "A", 1) == 3201
    assert P.pad_node(6, "D", 12) == 8512
    assert P.project_base(7000) == 5 and P.project_base(6000) == 4


def test_sample_root_page_parses_capacity():
    root = next(f for f in FRAMES if f["label"].startswith("preflight/sample_root"))
    body = P.parse_metadata_page(bytes.fromhex(root["response_hex"]))
    m = json.loads(body.split(b"\0", 1)[0])
    assert m["max_capacity"] == 62853120
    assert "free_space_in_bytes" in m
