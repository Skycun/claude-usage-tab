#!/usr/bin/env python3
"""Deciding when to hand the work over to another Claude account.

Two facts a front-end needs before offering a switch:

* :func:`running_sessions` — how many ``claude`` processes are alive. Not to
  refuse the switch, but to tell the user what it is about to affect.
* :func:`pick_offer` — whether there is somewhere worth going, once the
  active account has run out of five-hour window.

**What a switch actually does, verified rather than assumed.** The obvious
model is that swapping ``~/.claude/.credentials.json`` only affects the next
``claude`` launch, and that a running session keeps the token it loaded at
startup. That is wrong, and it was worth an afternoon to find out: a session
that was authenticated as one account reported the *other* one in ``/status``
about forty minutes after the swap, and refreshed that other account's token
back into the file. Running sessions follow the file. So a switch moves every
open terminal at once, which is what makes the feature worth having and why
nothing here launches a replacement terminal.

That behaviour is undocumented, exactly like the usage endpoint this project
already leans on. Treat it as observed, not guaranteed: if a future Claude
Code pins its credentials at startup, the switch quietly reverts to
"next launch only" and the offer becomes less useful, never harmful.

Read-only with respect to ``~/.claude``: this module inspects, it never
writes. The writing stays in ``accounts.switch_to``.

Stdlib only, no UI, no imports from this project — all three front-ends use
it, and the process probe must work headless.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# A process is a Claude Code session if its executable is named ``claude``
# (the native installer drops ``claude.exe`` in ``~/.local/bin``) or if its
# command line names the npm-installed CLI entry point.
_CLI_MARKERS = (
    "@anthropic-ai/claude-code",
    "claude/cli.js",
    "claude\\cli.js",
)
# ...but never *us*. The daemon runs as pythonw/python and would not match
# the rules above anyway; this is belt and braces.
_SELF_MARKERS = ("claude_usage", "claude-usage", "attention.py")

# The probe runs on an explicit click, never on the poll loop, so it can
# afford a real process query. It still needs a ceiling: a hung WMI provider
# must not park the worker thread for ever.
PROBE_TIMEOUT = 15
_CREATE_NO_WINDOW = 0x08000000

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


@dataclass(frozen=True)
class Offer:
    """A worthwhile handoff: this account, right now."""

    account_id: str
    email: str
    util: float


def pick_offer(
    active_util: float,
    candidates: "list[tuple[str, str, float]]",
) -> Offer | None:
    """Choose where to send the user when the active account runs dry.

    ``candidates`` are ``(account_id, email, five-hour utilisation)`` for the
    *other* stored accounts, already polled by the caller — this module does
    no network of its own. Pure and side-effect free so all three front-ends
    apply one policy instead of three.
    """
    if active_util < LIMIT_UTIL:
        return None
    usable = [c for c in candidates if c[2] < ROOM_UTIL]
    if not usable:
        return None
    account_id, email, util = min(usable, key=lambda c: c[2])
    return Offer(account_id=account_id, email=email, util=util)


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
    look" and "nothing is running" are different answers, and the sentence
    the confirmation shows is worded differently for each.
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


def main(argv: list[str]) -> int:
    """CLI: ``probe`` — how many Claude Code sessions are running."""
    probe = running_sessions()
    print(f"sessions={probe.count} known={probe.known}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
