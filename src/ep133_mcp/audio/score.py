"""score_pattern: how close a transcribed tick pattern is to a hand-played one.

Both patterns are in the generate_ppak events form ({bars, events: [{pad,
tick, ...}]}). The candidate is tiled or folded to the truth's length, then
per pad each truth hit takes the nearest unmatched candidate hit within
TOLERANCE_TICKS (half a 16th) on the circular bar, earliest truth hit first.
Reported per pad: hits on each side, matched, precision, recall, the
detected/truth count ratio and the mean |offset| in ticks; overall recall
and precision over every pad. `accepted` is the step-1 bar from
docs/handoff/session-2026-09-12-velocity-handoff.md: recall >= ACCEPT_RECALL
and no pad more than ACCEPT_RATIO times the truth's count.

A pad the truth plays and the candidate does not scores recall 0 for that
pad; a pad only the candidate plays scores precision 0 and ratio inf.
"""

from __future__ import annotations

import math

TICKS_PER_BAR = 384
TOLERANCE_TICKS = 12
ACCEPT_RECALL = 0.8
ACCEPT_RATIO = 1.5


def _hits_per_pad(pattern: dict, length: int) -> dict[int, list[int]]:
    """Ticks per pad, tiled up to or folded down to `length` ticks."""
    own = pattern["bars"] * TICKS_PER_BAR
    hits: dict[int, list[int]] = {}
    for event in pattern["events"]:
        for offset in range(0, max(length, own), own):
            tick = (event["tick"] + offset) % length
            if event["tick"] + offset < max(length, own):
                hits.setdefault(int(event["pad"]), []).append(tick)
    return {pad: sorted(ticks) for pad, ticks in hits.items()}


def _circular(a: int, b: int, length: int) -> int:
    d = abs(a - b) % length
    return min(d, length - d)


def score_pattern(candidate: dict, truth: dict, tolerance: int = TOLERANCE_TICKS) -> dict:
    length = truth["bars"] * TICKS_PER_BAR
    truth_hits = _hits_per_pad(truth, length)
    found_hits = _hits_per_pad(candidate, length)
    pads: dict[str, dict] = {}
    total_truth = total_found = total_matched = 0
    for pad in sorted(set(truth_hits) | set(found_hits)):
        want = truth_hits.get(pad, [])
        have = found_hits.get(pad, [])
        free = list(have)
        offsets: list[int] = []
        for tick in want:
            best = min(free, key=lambda t: _circular(t, tick, length), default=None)
            if best is not None and _circular(best, tick, length) <= tolerance:
                free.remove(best)
                offsets.append(_circular(best, tick, length))
        matched = len(offsets)
        pads[str(pad)] = {
            "truth": len(want), "detected": len(have), "matched": matched,
            "precision": round(matched / len(have), 3) if have else 0.0,
            "recall": round(matched / len(want), 3) if want else 0.0,
            "ratio": round(len(have) / len(want), 2) if want else math.inf,
            "mean_offset_ticks": round(sum(offsets) / matched, 1) if matched else None,
        }
        total_truth += len(want)
        total_found += len(have)
        total_matched += matched
    recall = total_matched / total_truth if total_truth else 0.0
    precision = total_matched / total_found if total_found else 0.0
    return {
        "pads": pads, "tolerance_ticks": tolerance,
        "truth": total_truth, "detected": total_found, "matched": total_matched,
        "recall": round(recall, 3), "precision": round(precision, 3),
        "accepted": recall >= ACCEPT_RECALL and all(p["ratio"] <= ACCEPT_RATIO for p in pads.values()),
    }
