#!/usr/bin/env python3
"""The one place that installs our hooks into ``~/.claude/settings.json``.

``attention.py`` knows how to *be* a hook; this module knows how to *register*
one. It is deliberately a separate file because it holds the project's second
write surface into ``~/.claude`` (the first is ``accounts.switch_to``), and a
write surface that lives in 60 lines of one small module is a write surface
you can audit in one sitting.

The contract it keeps:

* **Additive.** Your own hooks for the same events are left exactly as they
  are; ours is appended alongside them. Claude Code runs every entry.
* **Idempotent.** Installing twice replaces our entry rather than stacking a
  second copy — ours is recognisable by the ``attention.py`` in its command.
* **Reversible.** :func:`uninstall` removes only entries pointing at our own
  script, and a timestamp-free backup is written to
  ``~/.claude/settings.json.cusi-bak`` before any change.
* **Scoped.** Only the ``hooks`` key is ever touched. Everything else in the
  file is re-serialised byte-for-byte from what was parsed.

Windows note: the command names ``pythonw.exe`` when one sits next to the
running interpreter, because ``python.exe`` would flash a console window on
every single turn. Paths are written with forward slashes and quoted, which
survives both ``cmd`` and a POSIX shell.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import attention

CLAUDE_DIR = Path.home() / ".claude"
CLAUDE_SETTINGS = CLAUDE_DIR / "settings.json"
BACKUP_PATH = CLAUDE_DIR / "settings.json.cusi-bak"

# Our script's own name, and the marker that tells our hook entries apart from
# the user's inside a shared event list.
SCRIPT_NAME = "attention.py"
EVENTS = tuple(attention.EVENT_ACTIONS)

SELFTEST_TIMEOUT = 20


@dataclass(frozen=True)
class Result:
    """Outcome of an install/uninstall attempt."""

    ok: bool
    detail: str = ""


def script_path() -> Path:
    """Absolute path of the hook script, next to this module."""
    return (Path(__file__).resolve().parent / SCRIPT_NAME).resolve()


def interpreter() -> Path:
    """The interpreter to name in the hook command.

    ``sys.executable`` is right on every platform but one: under Windows the
    daemon may itself have been started by ``python.exe``, and baking that in
    would pop a console window on every single turn. ``winshell`` already
    knows how to find the windowless twin (including the ``python3.13.exe``
    case), so it is imported lazily here rather than re-derived — and lazily
    so the two POSIX front-ends never touch it.
    """
    exe = Path(sys.executable)
    if os.name != "nt":
        return exe
    try:
        import winshell

        return winshell.windowless_python(exe)
    except ImportError:
        return exe


def command() -> str:
    """The shell command Claude Code will run for every hooked event."""
    exe = str(interpreter()).replace("\\", "/")
    script = str(script_path()).replace("\\", "/")
    return f'"{exe}" "{script}" hook'


def _entry(cmd: str) -> dict:
    """One hook entry in Claude Code's schema.

    ``async`` matters: the flag write is a side show, and a turn must never
    wait on it.
    """
    return {"hooks": [{"type": "command", "command": cmd, "async": True}]}


def _is_ours(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    for hook in entry.get("hooks") or []:
        if isinstance(hook, dict) and SCRIPT_NAME in str(hook.get("command") or ""):
            return True
    return False


def _load() -> dict:
    """Parse ``~/.claude/settings.json``, or ``{}`` when it isn't usable."""
    try:
        raw = json.loads(CLAUDE_SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _hooks_of(data: dict) -> dict:
    hooks = data.get("hooks")
    return dict(hooks) if isinstance(hooks, dict) else {}


def installed() -> bool:
    """True when every event we need already carries one of our entries."""
    hooks = _hooks_of(_load())
    for event in EVENTS:
        entries = hooks.get(event)
        if not isinstance(entries, list) or not any(_is_ours(e) for e in entries):
            return False
    return True


def snippet() -> dict:
    """The ``hooks`` block on its own — for display, docs, or a manual paste."""
    cmd = command()
    return {event: [_entry(cmd)] for event in EVENTS}


def snippet_text() -> str:
    return json.dumps({"hooks": snippet()}, indent=2)


def _backup(data_text: str) -> None:
    """Keep the previous file verbatim. Best-effort: never blocks the write."""
    try:
        CLAUDE_DIR.mkdir(parents=True, exist_ok=True)
        BACKUP_PATH.write_text(data_text, encoding="utf-8")
    except OSError:
        pass


def _write(data: dict) -> Result:
    try:
        CLAUDE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CLAUDE_SETTINGS.with_suffix(".json.cusi-tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, CLAUDE_SETTINGS)
    except OSError as e:
        return Result(False, str(e))
    return Result(True)


def _apply(add: bool) -> Result:
    """Rewrite the ``hooks`` key with our entries added or removed."""
    try:
        previous = CLAUDE_SETTINGS.read_text(encoding="utf-8")
    except OSError:
        previous = ""
    if previous.strip():
        try:
            json.loads(previous)
        except ValueError:
            return Result(False, f"{CLAUDE_SETTINGS} is not valid JSON")
        _backup(previous)

    data = _load()
    hooks = _hooks_of(data)
    cmd = command()
    for event in EVENTS:
        entries = hooks.get(event)
        kept = [e for e in entries if not _is_ours(e)] if isinstance(entries, list) else []
        if add:
            kept.append(_entry(cmd))
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event, None)

    if hooks:
        data["hooks"] = hooks
    else:
        data.pop("hooks", None)
    return _write(data)


def install() -> Result:
    return _apply(True)


def uninstall() -> Result:
    return _apply(False)


def selftest() -> Result:
    """Run the hook command once with a synthetic payload and check the flag.

    Worth the two seconds it costs: the hook only ever runs inside Claude
    Code's own shell, so a quoting mistake would otherwise show up as "the
    icon never blinks" with nothing to look at.
    """
    session_id = "cusi-selftest"
    payload = json.dumps(
        {
            "hook_event_name": "Stop",
            "session_id": session_id,
            "cwd": str(Path.cwd()),
        }
    )
    attention.clear(session_id)
    try:
        subprocess.run(
            command(),
            shell=True,
            input=payload.encode("utf-8"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=SELFTEST_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return Result(False, f"{type(e).__name__}: {e}")

    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if any(s.session_id == session_id for s in attention.scan().sessions):
            attention.clear(session_id)
            return Result(True)
        time.sleep(0.1)
    return Result(False, "no flag written")


def main(argv: list[str]) -> int:
    """CLI: ``install`` / ``uninstall`` / ``status`` / ``snippet`` / ``selftest``."""
    action = argv[1] if len(argv) > 1 else "status"
    if action == "install":
        result = install()
    elif action == "uninstall":
        result = uninstall()
    elif action == "selftest":
        result = selftest()
    elif action == "snippet":
        print(snippet_text())
        return 0
    else:
        print(f"installed: {installed()}")
        print(f"command:   {command()}")
        return 0
    print("ok" if result.ok else f"failed: {result.detail}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
