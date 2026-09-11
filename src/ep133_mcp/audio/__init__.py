"""Reference-song pipeline: fetch a clip, analyze it, cut a kit from it.

Everything here is optional: the `audio` extra and ffmpeg are required at call
time, never at import time, so the core install stays small.
"""

from .errors import AudioError, AudioToolsUnavailable, FetchFailed, InvalidReference

__all__ = ["AudioError", "AudioToolsUnavailable", "FetchFailed", "InvalidReference"]
