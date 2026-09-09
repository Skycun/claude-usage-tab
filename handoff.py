#!/usr/bin/env python3
"""Hand a working session over to another Claude account.

``accounts.switch_to`` swaps the credentials on disk, which only affects the
*next* ``claude`` launch. This module supplies the three facts a front-end
needs to turn that into a usable "switch and carry on here":

* :func:`running_sessions` — is a ``claude`` process still alive? This is the
  one that matters, and not for the reason people expect. A live session
  keeps its access token in memory, so it is unaffected by the swap; but when
  that token expires the session **refreshes it and writes the result back**
  to ``~/.claude/.credentials.json``. A switch performed underneath a running
  session can therefore be undone minutes later, silently, with no error
  anywhere. Ask before switching, not after.
* :func:`recent_projects` — the directories worth resuming in, newest first,
  read straight from the ``projects`` map in ``~/.claude.json`` (each entry
  carries a ``lastStartTime``). Absolute paths, so nothing has to be decoded
  back from the transcript directory names, which is lossy.
* :func:`resume_argv` — how to actually start ``claude --continue``.
  Transcripts are keyed by working directory, not by account, so a
  conversation started under one account replays fine under another. The
  binary is *located* rather than assumed: the daemon is started by an
  autostart entry with a stripped PATH, the same trap ``costs`` documents.

Read-only with respect to ``~/.claude``: this module inspects, it never
writes. The writing stays in ``accounts.switch_to``.

Stdlib only, no UI, no imports from this project — all three front-ends use
it, and the process probe must work headless.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path.home() / ".claude.json"

# A process is a Claude Code session if its executable is named ``claude``
# (the native installer drops ``claude.exe`` in ``~/.local/bin``) or if its
# command line names the npm-installed CLI entry point.
_CLI_MARKERS = (
    "@anthropic-ai/claude-code",
    "claude/cli.js",
    "claude\\cli.js",
)
# ...but never *us*. The daemon runs as pythonw/python and would not match
# the rules above anyway; this is belt and braces, and it also spares the
# hook process during a burst of Stop events.
_SELF_MARKERS = ("claude_usage", "claude-usage", "attention.py")

# The probe runs on an explicit click, never on the poll loop, so it can
# afford a real process query. It still needs a ceiling: a hung WMI provider
# must not park the worker thread for ever.
PROBE_TIMEOUT = 15
_CREATE_NO_WINDOW = 0x08000000

DEFAULT_PROJECT_LIMIT = 6

# When to offer a handoff, and what counts as somewhere worth going. An
# account already at 90% would buy minutes, not an afternoon, so it is not
# offered — the user can still switch to it by hand from the accounts menu.
LIMIT_UTIL = 100.0
ROOM_UTIL = 90.0


@dataclass(frozen=True)
class Probe:
    """What we found when we looked for running Claude Code sessions."""

    count: int = 0
    known: bool = True

    @property
    def busy(self) -> bool:
        return self.count > 0

    @property
    def risky(self) -> bool:
        """True when switching now could be reverted, or we cannot tell."""
        return self.busy or not self.known


@dataclass(frozen=True)
class Project:
    """A directory Claude Code has been run in."""

    path: str
    name: str
    last_used: float  # epoch seconds; 0.0 when unknown


@dataclass(frozen=True)
class Offer:
    """A worthwhile handoff: this account, this folder, right now."""

    account_id: str
    email: str
    util: float
    project: Project


def pick_offer(
    active_util: float,
    candidates: "list[tuple[str, str, float]]",
    project: Project | None,
) -> Offer | None:
    """Choose where to send the user when the active account runs dry.

    ``candidates`` are ``(account_id, email, five-hour utilisation)`` for the
    *other* stored accounts, already polled by the caller — this module does
    no network of its own. Pure and side-effect free so all three front-ends
    apply one policy instead of three.
    """
    if active_util < LIMIT_UTIL or project is None:
        return None
    usable = [c for c in candidates if c[2] < ROOM_UTIL]
    if not usable:
        return None
    account_id, email, util = min(usable, key=lambda c: c[2])
    return Offer(account_id=account_id, email=email, util=util, project=project)


# --------------------------------------------------------------- process probe


def _is_session(name: str, cmdline: str) -> bool:
    lowered = cmdline.lower()
    if any(marker in lowered for marker in _SELF_MARKERS):
        return False
    stem = Path(name).stem.lower() if name else ""
    if stem == "claude":
        return True
    return any(marker in lowered for marker in _CLI_MARKERS)


def running_sessions() -> Probe:
    """Count live Claude Code sessions. Never raises.

    A failed probe returns ``known=False`` rather than zero: "we could not
    look" and "nothing is running" must not be confused by the caller, since
    only one of them is safe to switch under.
    """
    try:
        if os.name == "nt":
            return _probe_windows()
        return _probe_posix()
    except Exception:  # noqa: BLE001 — a probe must never break the caller
        return Probe(0, known=False)


def _run(argv: list[str]) -> str | None:
    """Run ``argv`` and return stdout, or ``None`` if it did not work out."""
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT,
            creationflags=_CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _probe_windows() -> Probe:
    """Query WMI for processes, then match names and command lines here.

    The matching deliberately happens in Python rather than in the
    PowerShell filter: one rule set, shared with the POSIX path, instead of
    two that can drift apart.
    """
    out = _run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            "Get-CimInstance Win32_Process |"
            " Select-Object Name,CommandLine | ConvertTo-Json -Compress",
        ]
    )
    if not out:
        return Probe(0, known=False)
    try:
        rows = json.loads(out)
    except ValueError:
        return Probe(0, known=False)
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list):
        return Probe(0, known=False)
    count = sum(
        1
        for row in rows
        if isinstance(row, dict)
        and _is_session(str(row.get("Name") or ""), str(row.get("CommandLine") or ""))
    )
    return Probe(count)


def _probe_posix() -> Probe:
    out = _run(["ps", "-eo", "pid=,comm=,args="])
    if out is None:
        return Probe(0, known=False)
    mine = str(os.getpid())
    count = 0
    for line in out.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 2 or parts[0] == mine:
            continue
        name = parts[1]
        args = parts[2] if len(parts) > 2 else ""
        if _is_session(name, args):
            count += 1
    return Probe(count)


# ------------------------------------------------------------ recent projects


def recent_projects(limit: int = DEFAULT_PROJECT_LIMIT) -> tuple[Project, ...]:
    """Directories Claude Code ran in, most recent first. Never raises.

    Sorted before the directories are stat'ed so a long history costs a
    handful of filesystem calls, not one per project — some of those paths
    are on removable or network volumes.
    """
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    projects = raw.get("projects") if isinstance(raw, dict) else None
    if not isinstance(projects, dict):
        return ()

    candidates: list[tuple[float, str]] = []
    for path, meta in projects.items():
        if not isinstance(path, str) or not path:
            continue
        stamp = 0.0
        if isinstance(meta, dict):
            try:
                stamp = float(meta.get("lastStartTime") or 0.0) / 1000.0
            except (TypeError, ValueError):
                stamp = 0.0
        candidates.append((stamp, path))
    candidates.sort(reverse=True)

    out: list[Project] = []
    for stamp, path in candidates[: max(1, limit) * 3]:
        try:
            if not Path(path).is_dir():
                continue
        except OSError:
            continue
        out.append(Project(path=path, name=Path(path).name or path, last_used=stamp))
        if len(out) >= limit:
            break
    return tuple(out)


def age_hours(project: Project, now: float | None = None) -> float | None:
    """Hours since that project was last opened, or ``None`` if unknown."""
    if not project.last_used:
        return None
    return max(0.0, ((now or time.time()) - project.last_used) / 3600.0)


# ------------------------------------------------------------- resume command


def _extra_bin_dirs() -> list[Path]:
    """Where the ``claude`` binary lands when PATH does not mention it.

    The native installer uses ``~/.local/bin`` on every platform (that is
    where ``claude.exe`` sits on Windows); the rest cover npm-global and
    Homebrew installs. Same stripped-PATH problem ``costs`` documents, but a
    different set of directories, so the list is not shared.
    """
    home = Path.home()
    dirs = [home / ".local" / "bin", home / ".claude" / "local"]
    if os.name == "nt":
        appdata = os.environ.get("APPDATA", "").strip()
        if appdata:
            dirs.append(Path(appdata) / "npm")
    else:
        dirs += [
            home / ".bun" / "bin",
            home / ".npm-global" / "bin",
            Path("/usr/local/bin"),
            Path("/opt/homebrew/bin"),
        ]
    return dirs


def claude_binary() -> Path | None:
    """Locate the ``claude`` executable, or ``None``.

    ``shutil.which`` both times so Windows applies PATHEXT for us: what we
    look for is ``claude``, what is on disk may be ``claude.exe`` or a
    ``.cmd`` shim.
    """
    found = shutil.which("claude")
    if found:
        return Path(found)
    extra = os.pathsep.join(str(d) for d in _extra_bin_dirs())
    if extra:
        found = shutil.which("claude", path=extra)
        if found:
            return Path(found)
    return None


def resume_argv() -> list[str] | None:
    """Argv that reopens the last conversation of whatever directory it runs in.

    ``--continue`` picks the most recent conversation for the working
    directory, which is why the caller sets ``cwd`` instead of passing a path
    here: the transcripts are keyed by directory, not by account, so this is
    what carries a conversation across a switch.
    """
    binary = claude_binary()
    return [str(binary), "--continue"] if binary else None


def main(argv: list[str]) -> int:
    """CLI: ``probe`` (default), ``projects``, ``binary``."""
    command = argv[1] if len(argv) > 1 else "probe"
    if command == "projects":
        for project in recent_projects(12):
            age = age_hours(project)
            when = f"{age:7.1f} h" if age is not None else "      ? "
            print(f"{when}  {project.path}")
    elif command == "binary":
        print(claude_binary() or "not found")
    else:
        probe = running_sessions()
        print(f"sessions={probe.count} known={probe.known} risky={probe.risky}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
