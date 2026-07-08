"""The 80%-session flourish engine, shared by both front-ends.

Given a :class:`~settings.SoundSettings` and the bundled ``assets`` directory,
this module decides what to play — audio and/or an image — for the current
mode and runs it, best-effort, on whichever platform we're on. Every action is
detached and wrapped: a missing file or an absent player degrades to silence,
never an exception that could reach the daemon's poll loop.

Kept UI-free so ``claude_usage_indicator`` (GTK) and ``claude_usage_menubar``
(rumps) share one code path — a fix lands once and both platforms benefit.

Playback tooling per platform:
- macOS: ``afplay`` for audio (incl. the system chime), ``open`` for images.
- Linux: the first available of a small player list for audio files (paplay
  and aplay can't decode mp3, so general players come first), a freedesktop
  event sound for the "classic" chime, and ``xdg-open`` for images.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from settings import SoundSettings

# Bundled "kylian" assets live in <install-dir>/assets/.
KYLIAN_AUDIO = "kylian.mp3"
KYLIAN_IMAGE = "kylian.jpg"

# macOS ships this reliably; Linux uses a freedesktop event sound instead.
_MACOS_CHIME = Path("/System/Library/Sounds/Glass.aiff")

# Linux audio-file players, first-available wins. General-purpose decoders
# come before paplay/aplay because those two can't play mp3 (our bundled
# asset is mp3). Each entry is (binary, argv_prefix) run as
# ``[binary, *prefix, path]``.
_LINUX_FILE_PLAYERS: tuple[tuple[str, list[str]], ...] = (
    ("ffplay", ["-nodisp", "-autoexit", "-loglevel", "quiet"]),
    ("mpv", ["--no-video", "--really-quiet"]),
    ("mpg123", ["-q"]),
    ("cvlc", ["--play-and-exit", "--intf", "dummy"]),
    ("paplay", []),
    ("aplay", ["-q"]),
)

# Linux "classic" chime candidates, first-available wins.
_LINUX_CHIME: tuple[list[str], ...] = (
    ["canberra-gtk-play", "-i", "message"],
    ["paplay", "/usr/share/sounds/freedesktop/stereo/message.oga"],
)


@dataclass(frozen=True)
class Flourish:
    """Concrete media resolved from a mode: what to actually play/open."""

    audio: Path | None = None
    image: Path | None = None
    system_chime: bool = False


def _existing(path: Path) -> Path | None:
    """Return ``path`` if it's an existing file, else ``None`` (never raises)."""
    try:
        return path if path.is_file() else None
    except OSError:
        return None


def resolve(sound: SoundSettings, assets_dir: Path) -> Flourish:
    """Map the current mode to concrete media.

    Ignores ``enabled`` on purpose — the real trigger gates on it via
    :func:`play_for`, while :func:`preview` (the settings *Test* button)
    deliberately plays regardless.
    """
    if sound.mode == "kylian":
        return Flourish(
            audio=_existing(assets_dir / KYLIAN_AUDIO),
            image=_existing(assets_dir / KYLIAN_IMAGE),
        )
    if sound.mode == "classic":
        return Flourish(system_chime=True)
    if sound.mode == "custom":
        return Flourish(
            audio=_existing(Path(sound.custom_audio)) if sound.custom_audio else None,
            image=_existing(Path(sound.custom_image)) if sound.custom_image else None,
        )
    return Flourish()


def play_for(sound: SoundSettings, assets_dir: Path) -> None:
    """Play the flourish for a real 80% crossing. No-op when disabled."""
    if not sound.enabled:
        return
    _play(resolve(sound, assets_dir))


def preview(sound: SoundSettings, assets_dir: Path) -> None:
    """Play the flourish now for the settings *Test* button (ignores enabled)."""
    _play(resolve(sound, assets_dir))


# ---------------------------------------------------------------- platform I/O


def _spawn(argv: list[str]) -> bool:
    """Launch ``argv`` detached, swallowing output. Returns success, never raises."""
    try:
        subprocess.Popen(
            argv,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except OSError:
        return False


def _play(fl: Flourish) -> None:
    if fl.audio is not None:
        _play_audio_file(fl.audio)
    elif fl.system_chime:
        _play_system_chime()
    if fl.image is not None:
        _open_image(fl.image)


def _play_audio_file(path: Path) -> None:
    if sys.platform == "darwin":
        _spawn(["afplay", str(path)])
        return
    for binary, prefix in _LINUX_FILE_PLAYERS:
        if shutil.which(binary):
            _spawn([binary, *prefix, str(path)])
            return


def _play_system_chime() -> None:
    if sys.platform == "darwin":
        if _MACOS_CHIME.is_file():
            _spawn(["afplay", str(_MACOS_CHIME)])
        return
    for argv in _LINUX_CHIME:
        if shutil.which(argv[0]):
            _spawn(argv)
            return


def _open_image(path: Path) -> None:
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    _spawn([opener, str(path)])
