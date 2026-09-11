"""Generate the Phase 0 test sample.

Synthesised here from scratch, so it carries no third-party rights and can be
committed as a fixture and uploaded to hardware without a licence question.

Written at exactly 46875 Hz mono / 16-bit — the EP-133's native rate — so the
upload path does no resampling and the bytes on the wire are the bytes here.

A 440 Hz tone with a fast decay and a short silent tail: audible and obviously
synthetic when it plays back from a pad, so it cannot be confused with any of
the user's own samples.

    python tools/make_test_wav.py
"""

import math
import struct
import wave
from pathlib import Path

RATE = 46875          # EP133_SAMPLE_RATE
DURATION_S = 0.40
FREQ_HZ = 440.0
DECAY_S = 0.12
OUT = Path(__file__).resolve().parent.parent / "fixtures" / "phase0-test-tone.wav"


def main():
    frames = int(RATE * DURATION_S)
    samples = []
    for n in range(frames):
        t = n / RATE
        env = math.exp(-t / DECAY_S)
        # keep well clear of full scale so nothing clips on the device
        value = 0.6 * env * math.sin(2.0 * math.pi * FREQ_HZ * t)
        samples.append(int(max(-1.0, min(1.0, value)) * 32767.0))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(OUT), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(struct.pack(f"<{len(samples)}h", *samples))

    size = OUT.stat().st_size
    print(f"wrote {OUT}")
    print(f"  {frames} frames, {DURATION_S}s, {RATE} Hz mono 16-bit")
    print(f"  {size} bytes on disk, {frames * 2} bytes of PCM payload")


if __name__ == "__main__":
    main()
