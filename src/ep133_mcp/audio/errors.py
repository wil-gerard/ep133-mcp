"""Structured failures for the reference-audio pipeline.

Shares the DeviceError base so server._error formats every tool failure the
same way: {error, message, ...detail}.
"""

from ..device import DeviceError


class AudioError(DeviceError):
    def __init__(self, what_failed, *, observed=None, expected=None, next_step):
        super().__init__(what_failed, what_failed=what_failed, observed=observed,
                         expected=expected, next_step=next_step)


class AudioToolsUnavailable(AudioError):
    """The optional audio extra or a system tool (ffmpeg, yt-dlp) is missing."""


class InvalidReference(AudioError):
    """The URL or time range cannot be used as given."""


class FetchFailed(AudioError):
    """Download, search or decode did not produce a clip."""
