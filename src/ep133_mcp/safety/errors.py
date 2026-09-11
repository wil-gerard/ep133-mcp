"""Structured failures shared by safety gates and MCP tools."""

from ..device import DeviceError


class SafetyError(DeviceError):
    def __init__(self, what_failed, *, observed=None, expected=None, next_step):
        super().__init__(what_failed, what_failed=what_failed, observed=observed,
                         expected=expected, next_step=next_step)


class InvalidBackup(SafetyError):
    pass


class BackupStale(SafetyError):
    pass


class UnsupportedFormat(SafetyError):
    pass


class TooLarge(SafetyError):
    pass


class InvalidDestination(SafetyError):
    pass


class NoSafeSlot(SafetyError):
    pass


class VerificationFailed(SafetyError):
    pass


class JournalError(SafetyError):
    pass
