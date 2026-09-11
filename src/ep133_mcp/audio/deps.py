"""Availability of the optional audio extra and its system tools.

The core install must not import numpy/librosa/soundfile. Everything here is
lazy so server_status can report what is missing without failing.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import sys

from .errors import AudioToolsUnavailable

EXTRA_INSTALL = "uv sync --extra audio"
SYSTEM_INSTALL = "brew install ffmpeg"
PYTHON_MODULES = ("numpy", "soundfile", "librosa", "yt_dlp", "demucs")


def which(name: str) -> str | None:
    """PATH lookup that also sees console scripts in this interpreter's venv."""
    found = shutil.which(name)
    if found:
        return found
    candidate = Path(sys.executable).parent / name
    return str(candidate) if candidate.is_file() else None


def probe() -> dict:
    """What server_status reports. Never raises."""
    missing = [m for m in PYTHON_MODULES if importlib.util.find_spec(m) is None]
    ffmpeg, yt_dlp = which("ffmpeg"), which("yt-dlp")
    return {
        "extra_installed": not missing,
        "missing_modules": missing,
        "ffmpeg": ffmpeg,
        "yt_dlp": yt_dlp,
        "install": {"extra": EXTRA_INSTALL, "system": SYSTEM_INSTALL},
    }


def require_modules(*modules: str) -> None:
    missing = [m for m in modules if importlib.util.find_spec(m) is None]
    if missing:
        raise AudioToolsUnavailable("Audio extra is not installed", observed={"missing_modules": missing},
                                    expected=f"{', '.join(modules)} importable",
                                    next_step=f"Run `{EXTRA_INSTALL}` in the server checkout, then retry.")


def require_tool(name: str) -> str:
    found = which(name)
    if found is None:
        step = SYSTEM_INSTALL if name == "ffmpeg" else EXTRA_INSTALL
        raise AudioToolsUnavailable(f"{name} is not on PATH", observed={"missing_tool": name},
                                    expected=f"{name} executable on PATH",
                                    next_step=f"Run `{step}` and retry.")
    return found
