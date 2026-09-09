#!/usr/bin/env python3
"""Which Claude Code sessions are waiting for you — the flag store.

A terminal that has just finished its turn, or that is stuck on a permission
prompt, has no way to reach the desktop on its own. Claude Code *does* fire
hooks for both moments, so this module is the two halves of that bridge:

* as a **hook** (``python attention.py hook``), it reads the event payload
  Claude Code writes on stdin and drops one small file per session under
  :data:`STATE_DIR` — or deletes it again when you come back to that terminal;
* as a **library**, :func:`scan` reads that directory back so a front-end can
  blink its tray icon while at least one session is flagged.

Design notes worth keeping:

* **One file per session, named by session id.** A counter could not tell
  "two terminals are done" from "one terminal finished twice", and could not
  be cleared per-session when you answer only one of them.
* **The id is sanitised before it becomes a filename.** It arrives from
  outside the process, so anything but ``[A-Za-z0-9_-]`` is dropped and the
  result is length-capped — a payload cannot walk out of ``STATE_DIR``.
* **The project *basename* is stored, never the full path.** Same hygiene
  rule as ``costs.json``: this cache is unencrypted and gets shared verbatim
  in bug reports, so it holds the folder name you need to tell two terminals
  apart and nothing more.
* **Everything is best-effort.** A hook that raises would surface as an error
  inside Claude Code, and a failed read must never break the poll loop, so
  both directions degrade to "no flags" instead of propagating.

Stdlib only, and no imports from this project: the hook runs as its own tiny
process on every turn, and the front-ends import it without dragging in GTK,
rumps or Pillow.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

APP_ID = "claude-usage-indicator"
STATE_DIR = Path.home() / ".cache" / APP_ID / "attention"

# The two states a flagged session can be in. ``waiting`` outranks ``done``
# everywhere: a permission prompt blocks the session, a finished turn doesn't.
KIND_WAITING = "waiting"
KIND_DONE = "done"
VALID_KINDS = (KIND_WAITING, KIND_DONE)

# Claude Code hook event -> what it does to that session's flag. ``Stop``
# fires when the turn ends, ``Notification`` when Claude asks for permission
# or has sat idle for a minute, and the last two mean you're back (or gone).
CLEAR = "clear"
EVENT_ACTIONS: dict[str, str] = {
    "Stop": KIND_DONE,
    "Notification": KIND_WAITING,
    "UserPromptSubmit": CLEAR,
    "SessionEnd": CLEAR,
}

# Top-bar markers for the two text shells (GNOME, macOS). ASCII only: the
# GNOME top-bar font drops most non-ASCII glyphs, which is the same reason
# ``topbar`` sanitises its separators. "!" reads as blocked, "*" as done.
MARKERS = {KIND_WAITING: "!", KIND_DONE: "*"}

_UNSAFE_ID = re.compile(r"[^A-Za-z0-9_-]")
MAX_ID_LEN = 64
MAX_PROJECT_LEN = 32
# A flag older than this is ignored and swept: a terminal killed with ^C never
# fires ``SessionEnd``, and nothing else would ever clear it.
DEFAULT_EXPIRE_MINUTES = 60


@dataclass(frozen=True)
class Session:
    """One flagged session, as read back from disk."""

    session_id: str
    kind: str
    ts: float
    project: str = ""


@dataclass(frozen=True)
class Snapshot:
    """Everything flagged right now, split by state."""

    waiting: tuple[Session, ...] = ()
    done: tuple[Session, ...] = ()

    @property
    def total(self) -> int:
        return len(self.waiting) + len(self.done)

    @property
    def kind(self) -> str | None:
        """The state to render, or ``None`` when nothing is flagged."""
        if self.waiting:
            return KIND_WAITING
        if self.done:
            return KIND_DONE
        return None

    @property
    def sessions(self) -> tuple[Session, ...]:
        """Both lists, blocking sessions first."""
        return self.waiting + self.done


def marker(kind: str | None) -> str:
    """Top-bar marker for a state, or "" when nothing is flagged."""
    return MARKERS.get(kind or "", "")


def safe_id(raw: object) -> str:
    """A session id reduced to something safe to use as a filename."""
    text = _UNSAFE_ID.sub("", str(raw or ""))[:MAX_ID_LEN]
    return text or "unknown"


def project_name(cwd: object) -> str:
    """The folder name of ``cwd`` — never the path leading to it."""
    try:
        name = Path(str(cwd or "")).name
    except (OSError, ValueError):
        return ""
    return name[:MAX_PROJECT_LEN]


def _path(session_id: str) -> Path:
    return STATE_DIR / (safe_id(session_id) + ".json")


def record(session_id: str, kind: str, cwd: str = "") -> bool:
    """Flag ``session_id``. Returns False if the write failed (never raises)."""
    if kind not in VALID_KINDS:
        return False
    path = _path(session_id)
    payload = {
        "kind": kind,
        "ts": time.time(),
        "project": project_name(cwd),
    }
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def clear(session_id: str) -> None:
    """Unflag one session. A missing file is the expected common case."""
    try:
        _path(session_id).unlink()
    except OSError:
        pass


def clear_all() -> int:
    """Unflag everything; returns how many flags were removed."""
    removed = 0
    for path in _files():
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def _files() -> list[Path]:
    try:
        return [p for p in STATE_DIR.iterdir() if p.suffix == ".json"]
    except OSError:
        return []


def _read(path: Path) -> Session | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("kind") or "")
    if kind not in VALID_KINDS:
        return None
    try:
        ts = float(raw.get("ts") or 0.0)
    except (TypeError, ValueError):
        ts = 0.0
    return Session(
        session_id=path.stem,
        kind=kind,
        ts=ts,
        project=str(raw.get("project") or "")[:MAX_PROJECT_LEN],
    )


def scan(expire_minutes: int = DEFAULT_EXPIRE_MINUTES) -> Snapshot:
    """Read every flag, dropping (and deleting) those older than the cutoff.

    Cheap enough for the blink timer: the directory holds one small file per
    live session, and an absent directory — the hooks were never installed —
    costs a single failed ``iterdir``.
    """
    cutoff = time.time() - max(1, expire_minutes) * 60
    waiting: list[Session] = []
    done: list[Session] = []
    for path in _files():
        session = _read(path)
        if session is None or session.ts < cutoff:
            try:
                path.unlink()
            except OSError:
                pass
            continue
        (waiting if session.kind == KIND_WAITING else done).append(session)
    waiting.sort(key=lambda s: s.ts)
    done.sort(key=lambda s: s.ts)
    return Snapshot(waiting=tuple(waiting), done=tuple(done))


# ------------------------------------------------------------------ hook entry


def _payload() -> dict:
    """The hook event Claude Code writes on stdin, or ``{}`` for anything else."""
    try:
        raw = sys.stdin.read()
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def handle_event(event: str, payload: dict) -> None:
    """Apply one hook event to the flag store."""
    action = EVENT_ACTIONS.get(event)
    if action is None:
        return
    session_id = safe_id(payload.get("session_id"))
    if action == CLEAR:
        clear(session_id)
        return
    record(session_id, action, str(payload.get("cwd") or ""))


def main(argv: list[str]) -> int:
    """CLI: ``hook [EventName]``, ``clear``, ``status``.

    Always exits 0. A hook that exits non-zero is reported as a failure inside
    Claude Code, and a desktop toy has no business interrupting a turn.
    """
    command = argv[1] if len(argv) > 1 else "hook"
    try:
        if command == "clear":
            clear_all()
        elif command == "status":
            snap = scan()
            print(
                json.dumps(
                    {
                        "waiting": len(snap.waiting),
                        "done": len(snap.done),
                        "sessions": [s.project or s.session_id for s in snap.sessions],
                    }
                )
            )
        else:
            payload = _payload()
            event = str(payload.get("hook_event_name") or "")
            if not event and len(argv) > 2:
                event = argv[2]
            handle_event(event, payload)
    except Exception:  # noqa: BLE001 — a hook must never fail the turn
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
