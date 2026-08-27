"""The 80%-session flourish engine, shared by all three front-ends.

Given a :class:`~settings.SoundSettings` and the bundled ``assets`` directory,
this module decides what to play — audio and/or an image — for the current
mode and runs it, best-effort, on whichever platform we're on. Every action is
detached and wrapped: a missing file or an absent player degrades to silence,
never an exception that could reach the daemon's poll loop.

Kept UI-free so ``claude_usage_indicator`` (GTK), ``claude_usage_menubar``
(rumps) and ``claude_usage_tray`` (pystray) share one code path — a fix lands
once and every platform benefits.

Playback tooling per platform:
- macOS: ``afplay`` for audio (incl. the system chime), ``open`` for images.
- Linux: the first available of a small player list for audio files (paplay
  and aplay can't decode mp3, so general players come first), a freedesktop
  event sound for the "classic" chime, and ``xdg-open`` for images.
- Windows: ``winsound`` for WAV and for the system chime, a hidden PowerShell
  ``MediaPlayer`` for everything else (``winsound`` only decodes WAV, and the
  bundled asset is an mp3), and ``os.startfile`` for images.
"""

from __future__ import annotations

import os
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

# Windows has no bundled command-line player, so anything winsound can't
# decode (i.e. anything but WAV) goes through WPF's MediaPlayer. The player
# only lives as long as its host process, hence the sleep: we wait out the
# real duration once the media has opened, capped so a user pointing "custom"
# at a two-hour podcast doesn't leave a process parked for two hours.
WINDOWS_AUDIO_CAP_SECONDS = 120
_CREATE_NO_WINDOW = 0x08000000
_PS_PLAY = (
    "Add-Type -AssemblyName PresentationCore;"
    "$p = New-Object System.Windows.Media.MediaPlayer;"
    "$p.Open([uri]'{path}');"
    "$p.Play();"
    "Start-Sleep -Milliseconds 400;"
    "$s = {cap};"
    "if ($p.NaturalDuration.HasTimeSpan)"
    " {{ $s = [Math]::Min($p.NaturalDuration.TimeSpan.TotalSeconds + 1, {cap}) }};"
    "Start-Sleep -Seconds $s"
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
    """Launch ``argv`` detached, swallowing output. Returns success, never raises.

    ``start_new_session`` is a no-op on Windows (subprocess ignores it there),
    so the console window has to be suppressed explicitly instead — otherwise
    the PowerShell helper flashes a black rectangle on every 80% crossing.
    """
    kwargs: dict = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = _CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(argv, **kwargs)
        return True
    except (OSError, ValueError):
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
    if sys.platform == "win32":
        _play_audio_windows(path)
        return
    for binary, prefix in _LINUX_FILE_PLAYERS:
        if shutil.which(binary):
            _spawn([binary, *prefix, str(path)])
            return


def _play_audio_windows(path: Path) -> None:
    """WAV through winsound, anything else through hidden PowerShell."""
    if path.suffix.lower() == ".wav" and _winsound_play(path):
        return
    script = _PS_PLAY.format(
        # PowerShell single-quoted strings escape a quote by doubling it.
        path=str(path).replace("'", "''"),
        cap=WINDOWS_AUDIO_CAP_SECONDS,
    )
    _spawn(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ]
    )


def _winsound_play(path: Path) -> bool:
    """Async WAV playback. ``False`` if winsound isn't there or refuses it."""
    try:
        import winsound  # Windows-only stdlib module
    except ImportError:
        return False
    try:
        winsound.PlaySound(
            str(path),
            winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
        )
        return True
    except (RuntimeError, OSError):
        return False


def _play_system_chime() -> None:
    if sys.platform == "darwin":
        if _MACOS_CHIME.is_file():
            _spawn(["afplay", str(_MACOS_CHIME)])
        return
    if sys.platform == "win32":
        try:
            import winsound

            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except (ImportError, RuntimeError, OSError):
            pass
        return
    for argv in _LINUX_CHIME:
        if shutil.which(argv[0]):
            _spawn(argv)
            return


def _open_image(path: Path) -> None:
    if sys.platform == "win32":
        try:
            os.startfile(str(path))  # type: ignore[attr-defined] — Windows only
        except (AttributeError, OSError):
            pass
        return
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    _spawn([opener, str(path)])
