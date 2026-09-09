"""Windows OS integration for the tray front-end.

Everything here talks to Windows and nothing here knows about usage data, so
``claude_usage_tray`` stays a presentation layer the way the GTK and rumps
apps do. It is the Windows counterpart of the ``osascript`` helpers at the top
of ``claude_usage_menubar.py``.

Only ``ctypes``, ``winreg`` and ``subprocess`` — all stdlib. Pulling in
``pywin32`` for a message box and a file picker would double the install
surface for maybe eighty lines of code.

Two rules hold throughout:

* **Fail-open.** Every function swallows its own errors and returns a
  falsy/neutral value. The daemon polls forever; a failed registry read or a
  missing ``powershell.exe`` must never propagate.
* **Import-safe off Windows.** ``ctypes.windll`` only exists on Windows, so
  it is touched lazily inside functions, never at import time. That keeps the
  module importable on Linux/macOS for tests.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import webbrowser
from pathlib import Path

APP_NAME = "Claude Usage Tab"
# HKCU Run entry name — also what uninstall-windows.ps1 deletes.
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "ClaudeUsageTab"

# Hard limits of the NOTIFYICONDATAW fields pystray writes into. ctypes raises
# ValueError on an over-long assignment, which would take the daemon down, so
# every string that reaches Shell_NotifyIcon goes through :func:`clamp` first.
MAX_TIP = 127          # szTip is WCHAR[128]
MAX_INFO = 255         # szInfo is WCHAR[256]
MAX_INFO_TITLE = 63    # szInfoTitle is WCHAR[64]

# CreateProcess flags.
CREATE_NEW_CONSOLE = 0x00000010
CREATE_NO_WINDOW = 0x08000000

# MessageBoxW.
_MB_OK = 0x0
_MB_YESNO = 0x4
_MB_ICONQUESTION = 0x20
_MB_ICONINFORMATION = 0x40
_MB_SETFOREGROUND = 0x10000
_MB_TOPMOST = 0x40000
_IDYES = 6


def is_windows() -> bool:
    return sys.platform == "win32"


def clamp(text: str, limit: int) -> str:
    """Truncate to ``limit`` characters, marking the cut with an ellipsis."""
    text = text or ""
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    return text[: limit - 1] + "…"


# ------------------------------------------------------------------ launching


def open_path(target: str) -> bool:
    """Open a file, folder or URL with its default handler."""
    try:
        os.startfile(target)  # type: ignore[attr-defined] — Windows only
        return True
    except AttributeError:
        return webbrowser.open(target)
    except OSError:
        # No association (a .json with nothing registered, say) — the browser
        # will at least display it.
        return webbrowser.open(target)


def spawn(
    argv: list[str], *, console: bool = False, cwd: str | None = None
) -> bool:
    """Launch ``argv`` detached from us. Returns success, never raises.

    ``console`` shows a window (update/uninstall, or an interactive tool the
    user is meant to type into); without it the child is fully silent, which
    is what audio playback and other background helpers want. ``cwd`` sets
    the child's working directory — the way to start an interactive tool *in*
    a project without building a shell command line and quoting a path into
    it.

    **``stdin`` follows ``console``, and it is not a detail.** A background
    helper must not inherit stdin, hence ``DEVNULL`` there. But a child given
    its own console needs that console's keyboard: hand it ``DEVNULL`` and it
    reads end-of-file on its first prompt and exits, which looks exactly like
    a window flashing open and vanishing. Passing ``None`` sets no handles at
    all in ``STARTUPINFO``, so Windows wires the child to the console it just
    created. This is what makes ``claude --continue`` survive here, and it is
    also why ``run_script_in_console`` can hold a window open on a
    ``Read-Host``.
    """
    try:
        subprocess.Popen(
            argv,
            cwd=cwd,
            creationflags=CREATE_NEW_CONSOLE if console else CREATE_NO_WINDOW,
            close_fds=True,
            stdin=None if console else subprocess.DEVNULL,
            stdout=None if console else subprocess.DEVNULL,
            stderr=None if console else subprocess.DEVNULL,
        )
        return True
    except (OSError, ValueError):
        return False


def powershell(script: str, *, console: bool = False) -> bool:
    """Run an inline PowerShell command. ``-NoProfile`` keeps it predictable."""
    return spawn(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        console=console,
    )


def run_script_in_console(script: Path, *args: str) -> bool:
    """Run a repo ``.ps1`` in a visible console, held open at the end.

    The Windows twin of ``_run_script_in_terminal`` in the GTK app: the update
    and uninstall paths kill this very process, so the output has to survive
    us and stay on screen.
    """
    quoted = " ".join(f"'{a}'" for a in args)
    inner = f"& '{script}' {quoted}".strip()
    wrapped = (
        f"{inner}; Write-Host ''; "
        "Read-Host 'Press Enter to close'"
    )
    return powershell(wrapped, console=True)


def open_claude_login() -> bool:
    """Open a console running ``claude`` so the user can sign in."""
    return powershell(
        "claude; Write-Host ''; Read-Host 'Press Enter to close'",
        console=True,
    )


# -------------------------------------------------------------------- dialogs


def confirm(title: str, body: str) -> bool:
    """Modal yes/no. ``False`` on cancel *and* on any failure to show it.

    Call this off the tray's message-loop thread: it blocks until answered,
    and the icon stops responding to clicks while it is up.
    """
    try:
        rc = ctypes.windll.user32.MessageBoxW(
            None,
            body,
            title,
            _MB_YESNO | _MB_ICONQUESTION | _MB_SETFOREGROUND | _MB_TOPMOST,
        )
    except (AttributeError, OSError):
        return False
    return rc == _IDYES


def alert(title: str, body: str) -> None:
    """Modal information box, used when a notification would be too quiet."""
    try:
        ctypes.windll.user32.MessageBoxW(
            None,
            body,
            title,
            _MB_OK | _MB_ICONINFORMATION | _MB_SETFOREGROUND | _MB_TOPMOST,
        )
    except (AttributeError, OSError):
        pass


# The comdlg32 file picker, laid out for 64-bit ctypes. Only the fields we set
# matter; the rest exist to give the struct its real size, which
# ``lStructSize`` must match or the call fails outright.
class _OPENFILENAMEW(ctypes.Structure):
    _fields_ = [
        ("lStructSize", ctypes.c_uint32),
        ("hwndOwner", ctypes.c_void_p),
        ("hInstance", ctypes.c_void_p),
        ("lpstrFilter", ctypes.c_wchar_p),
        ("lpstrCustomFilter", ctypes.c_wchar_p),
        ("nMaxCustFilter", ctypes.c_uint32),
        ("nFilterIndex", ctypes.c_uint32),
        ("lpstrFile", ctypes.c_wchar_p),
        ("nMaxFile", ctypes.c_uint32),
        ("lpstrFileTitle", ctypes.c_wchar_p),
        ("nMaxFileTitle", ctypes.c_uint32),
        ("lpstrInitialDir", ctypes.c_wchar_p),
        ("lpstrTitle", ctypes.c_wchar_p),
        ("Flags", ctypes.c_uint32),
        ("nFileOffset", ctypes.c_uint16),
        ("nFileExtension", ctypes.c_uint16),
        ("lpstrDefExt", ctypes.c_wchar_p),
        ("lCustData", ctypes.c_void_p),
        ("lpfnHook", ctypes.c_void_p),
        ("lpTemplateName", ctypes.c_wchar_p),
        ("pvReserved", ctypes.c_void_p),
        ("dwReserved", ctypes.c_uint32),
        ("FlagsEx", ctypes.c_uint32),
    ]


_OFN_FILEMUSTEXIST = 0x00001000
_OFN_PATHMUSTEXIST = 0x00000800
_OFN_NOCHANGEDIR = 0x00000008
_OFN_EXPLORER = 0x00080000

AUDIO_FILTER = ("Audio", "*.mp3;*.wav;*.wma;*.m4a;*.aac;*.ogg;*.flac")
IMAGE_FILTER = ("Images", "*.png;*.jpg;*.jpeg;*.gif;*.bmp;*.webp")


def choose_file(title: str, spec: tuple[str, str]) -> str | None:
    """Native "Open file" dialog. ``None`` on cancel or on any failure.

    Like :func:`confirm`, this is modal — keep it off the message-loop thread.
    """
    label, patterns = spec
    # The filter is a double-NUL-terminated list of NUL-separated pairs.
    filt = f"{label} ({patterns})\0{patterns}\0All files (*.*)\0*.*\0\0"
    buf = ctypes.create_unicode_buffer(4096)
    ofn = _OPENFILENAMEW()
    ofn.lStructSize = ctypes.sizeof(_OPENFILENAMEW)
    ofn.lpstrFilter = filt
    ofn.nFilterIndex = 1
    ofn.lpstrFile = ctypes.cast(buf, ctypes.c_wchar_p)
    ofn.nMaxFile = len(buf)
    ofn.lpstrTitle = title
    ofn.Flags = (
        _OFN_FILEMUSTEXIST | _OFN_PATHMUSTEXIST | _OFN_NOCHANGEDIR | _OFN_EXPLORER
    )
    try:
        ok = ctypes.windll.comdlg32.GetOpenFileNameW(ctypes.byref(ofn))
    except (AttributeError, OSError):
        return None
    if not ok:
        return None  # cancelled, or the dialog refused to open
    return buf.value or None


# ------------------------------------------------------------------- autostart


def windowless_python(exe: Path) -> Path:
    """The ``pythonw.exe`` beside ``exe``, or ``exe`` when there isn't one.

    Autostart must not flash a console window at every sign-in, so the Run
    entry has to name the windowless interpreter. A blunt
    ``name.replace("python", "pythonw")`` gets this wrong twice: it turns an
    already-windowless ``pythonw.exe`` into ``pythonww.exe``, and a versioned
    ``python3.13.exe`` into ``pythonw3.13.exe`` — neither exists, so it would
    quietly fall back to the console interpreter. The ``w`` goes after the
    ``python`` stem instead, with a plain ``pythonw`` as the last resort.
    """
    stem = exe.stem
    if stem.lower().startswith("pythonw"):
        return exe
    if not stem.lower().startswith("python"):
        return exe
    for candidate in (
        exe.with_name(f"pythonw{stem[len('python'):]}{exe.suffix}"),
        exe.with_name(f"pythonw{exe.suffix}"),
    ):
        try:
            if candidate.exists():
                return candidate
        except OSError:
            continue
    return exe


def startup_command(script: Path) -> str:
    """The HKCU\\Run command line: ``pythonw.exe`` so no console flashes up."""
    return f'"{windowless_python(Path(sys.executable))}" "{script}"'


def startup_enabled() -> bool:
    """Whether our HKCU\\Run entry exists (its exact command isn't checked)."""
    try:
        import winreg
    except ImportError:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, RUN_VALUE)
        return bool(value)
    except (OSError, ValueError):
        return False


def set_startup(enabled: bool, script: Path) -> bool:
    """Add or remove the HKCU\\Run entry. Returns whether it worked.

    HKCU only — never HKLM. Writing a machine-wide autostart entry would need
    elevation and would affect other users of the PC, neither of which a tray
    app has any business doing.
    """
    try:
        import winreg
    except ImportError:
        return False
    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enabled:
                winreg.SetValueEx(
                    key, RUN_VALUE, 0, winreg.REG_SZ, startup_command(script)
                )
            else:
                try:
                    winreg.DeleteValue(key, RUN_VALUE)
                except FileNotFoundError:
                    pass  # already gone — the requested state, so: success
        return True
    except OSError as e:
        print(f"winshell: autostart write failed: {e}", file=sys.stderr)
        return False
