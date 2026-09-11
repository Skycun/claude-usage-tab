"""Application version — single source of truth.

Read from the sibling ``VERSION`` file so the shell scripts (``install.sh``,
``update.sh``) and any future packaging can share the exact same value.
Falls back to a baked-in default if the file is missing (e.g. a partial or
in-place edit). A leaf module — imports nothing from the project.
"""

from __future__ import annotations

from pathlib import Path

_FALLBACK = "1.3.0"


def current_version() -> str:
    """Return the app version string (e.g. ``"1.1.0"``)."""
    try:
        path = Path(__file__).resolve().parent / "VERSION"
        return path.read_text(encoding="utf-8").strip() or _FALLBACK
    except (OSError, UnicodeDecodeError):
        return _FALLBACK


__version__ = current_version()
