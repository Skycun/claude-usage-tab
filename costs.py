"""API cost report, backed by ``ccusage`` (opt-in, dropdown-only).

Claude Code writes every request it makes to ``~/.claude/projects/**/*.jsonl``.
`ccusage <https://github.com/ryoppippi/ccusage>`_ parses those transcripts and
prices them, which gives us something the OAuth usage endpoint never exposes:
what the same traffic *would* cost on the API, per day. We shell out to it
rather than re-implementing the pricing tables (they drift with every model
release) — the tool is the single source of truth, we only aggregate its JSON.

Design constraints, in order of importance:

* **Three gates before anything shows.** ``cost.enabled`` (off by default),
  a platform whose front-end path has actually been exercised, and a runner
  that exists on this machine — see :func:`is_available`.
* **Opt-in and fail-open.** ``cost.enabled`` is off by default. No runner
  installed, a non-zero exit, malformed JSON, a timeout — every failure path
  returns the last cached report (or ``None``) and never raises into the
  daemon. A missing cost row hides a menu item; it never breaks the tray.
* **Never on the UI thread.** ``check()`` spawns a subprocess that routinely
  takes seconds. Both front-ends call it from a worker thread and apply the
  result back on their main loop.
* **Throttled.** A run walks a few hundred megabytes of transcripts, so the
  cadence is minutes, not the 2-minute usage poll. :data:`HARD_FLOOR` caps
  even forced refreshes so a click-spamming user can't fork a process storm.

Privacy: the cache holds ``(date, amount)`` pairs only — no path, no project
name, no account, in the same spirit as the ``history.jsonl`` rule.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from settings import APP_ID, CostSettings

CACHE_DIR = Path.home() / ".cache" / APP_ID
CACHE_PATH = CACHE_DIR / "costs.json"

# Hard floor between two runs, even when forced (menu "refresh"). Keeps a
# double-click — or a restart loop — from spawning a second scan.
HARD_FLOOR = 30
# How long we let the runner work before giving up. Generous on purpose: a
# cold ``bunx`` downloads the package first, which can take tens of seconds
# on a slow link. It runs in a worker thread, so waiting costs nothing.
RUN_TIMEOUT = 90

# Runners we know how to drive, best first. A real install wins — no
# package resolution round-trip at all. Between the three shims the order
# barely matters: measured warm, bunx/npx/pnpm land within noise of each
# other (~3-5 s) because the transcript scan dominates, not the launcher.
# So they're listed by how widespread they are, not by speed.
#
# Yarn is deliberately absent: ``dlx`` only exists in Yarn 2+, detecting the
# major would cost an extra spawn, and in practice anyone with yarn also has
# npx — it would add complexity and no coverage.
_RUNNERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ccusage", ()),
    ("bunx", ("ccusage@latest",)),
    ("npx", ("-y", "ccusage@latest")),
    ("pnpm", ("dlx", "ccusage@latest")),
)

# The daemon is usually started by an autostart .desktop entry or a
# LaunchAgent, which hands it a minimal PATH — none of the per-user Node
# install locations are on it. Look there ourselves before giving up.
def _extra_bin_dirs() -> list[Path]:
    home = Path.home()
    dirs = [
        home / ".bun" / "bin",
        home / ".local" / "bin",
        home / ".local" / "share" / "pnpm",
        home / ".volta" / "bin",
        home / ".npm-global" / "bin",
        home / ".yarn" / "bin",
        Path("/usr/local/bin"),
        Path("/opt/homebrew/bin"),
    ]
    # nvm keeps one bin dir per installed Node; newest name wins (string sort
    # is good enough to prefer v22 over v20 — an exact ordering doesn't
    # matter, any working npx will do).
    nvm = home / ".nvm" / "versions" / "node"
    try:
        dirs.extend(sorted((p / "bin" for p in nvm.iterdir() if p.is_dir()), reverse=True))
    except OSError:
        pass
    return dirs


def _locate(name: str) -> Path | None:
    """Find an executable on PATH, then in the usual per-user Node dirs."""
    found = shutil.which(name)
    if found:
        return Path(found)
    for d in _extra_bin_dirs():
        candidate = d / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def resolve_command(configured: str = "") -> list[str] | None:
    """Return the argv prefix that runs ccusage, or ``None`` if unavailable.

    A non-empty ``configured`` string (settings ``cost.command``) wins — the
    escape hatch for exotic setups (a pinned version, a wrapper script,
    ``deno run``…). Its arguments are used verbatim, but its executable still
    has to exist: a typo has to surface as "no runner" in the menu and in the
    settings dialog, not as a mystery empty row once a spawn fails.
    Otherwise we auto-detect.
    """
    if configured.strip():
        try:
            argv = shlex.split(configured)
        except ValueError:  # unbalanced quotes
            return None
        if not argv:
            return None
        binary = _locate(argv[0])
        if binary is None:
            return None
        return [str(binary), *argv[1:]]
    for name, args in _RUNNERS:
        binary = _locate(name)
        if binary:
            return [str(binary), *args]
    return None


def _run_env(argv: list[str]) -> dict[str, str]:
    """Env for the child, with the runner's own dir prepended to PATH.

    ``npx`` and corepack's ``pnpm`` are Node scripts: invoking one by absolute
    path still leaves its ``#!/usr/bin/env node`` shebang looking ``node`` up
    on PATH. Putting its sibling directory first makes an absolute-path
    invocation work under the daemon's stripped-down PATH.

    Deliberately **not** ``resolve()``d: corepack ships ``pnpm``/``yarn`` as
    symlinks into ``lib/node_modules/corepack/dist``, so resolving would hand
    the child that dist directory — where there is no ``node`` — instead of
    the bin directory that has one. Exit 127, every time.
    """
    env = dict(os.environ)
    bin_dir = str(Path(argv[0]).parent)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}".rstrip(os.pathsep)
    # ccusage colourises when it thinks a TTY is attached; belt and braces.
    env["NO_COLOR"] = "1"
    return env


# The macOS front-end's cost path — worker thread parking a result for a
# ``rumps.Timer`` to pick up, menu rebuilt around it — has not been run on a
# real Mac yet. Until it has, the feature stays Linux-only rather than
# shipping an unexercised path to menu-bar users: no row, no toggle, no
# subprocess. Flip this to True once it's been exercised on macOS.
MACOS_READY = False


def platform_supported() -> bool:
    return MACOS_READY or sys.platform != "darwin"


def runner_available(configured: str = "") -> bool:
    """Whether *some* ccusage runner exists on this machine."""
    return resolve_command(configured) is not None


def is_available(cs: CostSettings) -> bool:
    """Whether the cost UI should appear at all.

    A missing runner hides the row entirely instead of parking a permanent
    error in the menu: nobody wants a dead row for a feature they can't use.
    The settings dialog is where the absence gets explained — at the moment
    you tick the box, which is when it's actionable.
    """
    return (
        cs.enabled
        and platform_supported()
        and runner_available(cs.command)
    )


@dataclass(frozen=True)
class CostReport:
    """Aggregated spend, in USD.

    ``checked_at`` is when the tool last *ran* (not when it last succeeded),
    and ``stale`` says that run failed — the numbers are the newest we have,
    but nothing confirmed them. The menu shows both, so a silently broken
    runner is visible instead of looking like "you spent nothing today".
    """

    today: float
    last_7d: float
    month: float
    checked_at: float
    stale: bool = False


def _load_cache() -> dict:
    try:
        data = json.loads(CACHE_PATH.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(data: dict) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(data, indent=2) + "\n")
    except OSError:
        pass


def _parse_json(stdout: str) -> dict | None:
    """Parse the report, tolerating banner lines before the JSON body."""
    start = stdout.find("{")
    if start < 0:
        return None
    try:
        parsed = json.loads(stdout[start:])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _daily_costs(payload: dict) -> dict[str, float]:
    """``{"2026-08-24": 12.4}`` from a ``ccusage daily --json`` payload.

    Every field is treated as optional: this is a third-party tool whose
    schema we don't control, exactly like the usage endpoint. Rows we can't
    read are skipped, not fatal.
    """
    out: dict[str, float] = {}
    rows = payload.get("daily")
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        # With ``--by-agent`` each period also carries per-agent rows; we ask
        # for the aggregate only, but guard in case a future version changes
        # the default shape.
        if row.get("agent") not in (None, "all"):
            continue
        period = row.get("period")
        if not isinstance(period, str):
            continue
        try:
            out[period] = out.get(period, 0.0) + float(row.get("totalCost") or 0)
        except (TypeError, ValueError):
            continue
    return out


def summarize(days: dict[str, float], today: date, checked_at: float,
              stale: bool = False) -> CostReport:
    """Fold per-day amounts into today / rolling 7 days / calendar month."""
    week_start = today - timedelta(days=6)
    month_prefix = today.strftime("%Y-%m")
    last_7d = 0.0
    month = 0.0
    for iso, amount in days.items():
        try:
            day = date.fromisoformat(iso)
        except ValueError:
            continue
        if week_start <= day <= today:
            last_7d += amount
        if iso.startswith(month_prefix):
            month += amount
    return CostReport(
        today=days.get(today.isoformat(), 0.0),
        last_7d=last_7d,
        month=month,
        checked_at=checked_at,
        stale=stale,
    )


def _since_arg(today: date) -> str:
    """Earliest day we need: the rolling week or the 1st of the month."""
    start = min(today.replace(day=1), today - timedelta(days=6))
    return start.strftime("%Y%m%d")


def _fetch(argv: list[str], today: date) -> dict[str, float] | None:
    """Run the tool once. ``None`` on any failure — never raises."""
    cmd = [*argv, "daily", "--json", "--since", _since_arg(today)]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=RUN_TIMEOUT,
            env=_run_env(argv),
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError) as e:
        print(f"costs: {argv[0]} failed: {type(e).__name__}: {e}", file=sys.stderr)
        return None
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()[-1:] or [""]
        print(f"costs: exit {proc.returncode}: {detail[0][:200]}", file=sys.stderr)
        return None
    payload = _parse_json(proc.stdout or "")
    if payload is None:
        print("costs: unreadable JSON from ccusage", file=sys.stderr)
        return None
    return _daily_costs(payload)


def cached(today: date | None = None) -> CostReport | None:
    """Last known report from cache only — no subprocess, instant.

    Lets the menu show a number the moment it's built, before the first
    background refresh of the session lands.
    """
    cache = _load_cache()
    days = cache.get("days")
    if not isinstance(days, dict) or not days:
        return None
    clean = {k: v for k, v in days.items() if isinstance(k, str) and isinstance(v, (int, float))}
    if not clean:
        return None
    return summarize(
        clean,
        today or date.today(),
        float(cache.get("checked_at") or 0),
        stale=not bool(cache.get("ok", True)),
    )


def check(cs: CostSettings, *, force: bool = False,
          now: float | None = None, today: date | None = None) -> CostReport | None:
    """Return the cost report, re-running the tool only when due.

    Call from a worker thread. Returns ``None`` only when the feature is off
    or no runner exists *and* nothing was ever cached; a failed run degrades
    to the cached numbers flagged ``stale`` so the menu stays populated.
    """
    if not is_available(cs):
        return None
    now = now if now is not None else time.time()
    today = today or date.today()
    cache = _load_cache()
    days = cache.get("days")
    days = days if isinstance(days, dict) else {}
    last = float(cache.get("checked_at") or 0)

    threshold = HARD_FLOOR if force else max(cs.refresh_minutes * 60, HARD_FLOOR)
    if (now - last) < threshold:
        return cached(today)

    argv = resolve_command(cs.command)
    if argv is None:
        return cached(today)

    fetched = _fetch(argv, today)
    # Advance the throttle clock on every *attempt*, success or not, so a
    # persistently broken runner can't be re-spawned on every tick.
    cache["checked_at"] = now
    cache["ok"] = fetched is not None
    if fetched is not None:
        # Replace only the window we asked for; older days stay untouched so
        # a narrow --since can't wipe history we already have.
        days.update(fetched)
        cache["days"] = days
    _save_cache(cache)
    return summarize(days, today, now, stale=fetched is None)
