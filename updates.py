"""GitHub release update check.

Asks ``github.com/<repo>/releases/latest`` whether a newer version exists,
throttled so we never hit the API more than needed, and caches the answer
under the cache dir. Pure data — no GTK, no application of the update (the
daemon spawns ``update.sh`` for that). Network failures degrade to "no
update" and never raise into the caller.

Privacy: the only request is an unauthenticated ``GET`` to the public
releases API — no token, no identifier, nothing about the user is sent.
The check is gated by ``update_check_enabled`` in settings.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from settings import APP_ID
from version import current_version

REPO = "Skycun/claude-usage-tab"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{REPO}/releases/latest"

CACHE_DIR = Path.home() / ".cache" / APP_ID
CACHE_PATH = CACHE_DIR / "update_check.json"

# How stale a cached result may be before a periodic tick re-checks (6h).
PERIODIC_INTERVAL = 6 * 3600
# Hard floor even for a forced (startup) check, so a restart loop can never
# hammer the API or leak activity by pinging on every relaunch.
HARD_FLOOR = 300


@dataclass(frozen=True)
class UpdateInfo:
    current: str
    latest: str | None  # None until a successful check has happened
    available: bool
    url: str


def _parse(tag: str | None) -> tuple[int, ...]:
    """Turn ``"v0.3.1"`` / ``"0.3"`` into ``(0, 3, 1)`` / ``(0, 3)``."""
    if not tag:
        return ()
    return tuple(int(n) for n in re.findall(r"\d+", tag))


def is_newer(latest: str | None, current: str) -> bool:
    # No explicit prerelease filtering: GitHub's ``releases/latest`` endpoint
    # already excludes drafts and prereleases, so ``latest`` is always a
    # stable tag. Numeric-only compare treats ``1.0.0-rc1`` == ``1.0.0``.
    latest_parts, current_parts = _parse(latest), _parse(current)
    return bool(latest_parts) and latest_parts > current_parts


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


def _fetch_latest_tag() -> str | None:
    """GET the latest release tag, or None on any failure.

    404 (no releases yet) and 403 (rate-limited) both degrade to None —
    the caller keeps whatever it had cached and retries later.
    """
    try:
        r = requests.get(
            LATEST_API,
            headers={"Accept": "application/vnd.github+json"},
            timeout=10,
        )
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    try:
        return (r.json() or {}).get("tag_name")
    except ValueError:
        return None


def check(*, force: bool = False, now: float | None = None) -> UpdateInfo:
    """Return the current update status, hitting the network only if due.

    ``force`` (used on startup) lowers the throttle to :data:`HARD_FLOOR`;
    periodic callers use :data:`PERIODIC_INTERVAL`. A failed fetch keeps the
    last cached ``latest`` so the UI stays stable across transient outages.
    """
    now = now if now is not None else time.time()
    current = current_version()
    cache = _load_cache()
    last = float(cache.get("checked_at") or 0)
    latest = cache.get("latest")

    threshold = HARD_FLOOR if force else PERIODIC_INTERVAL
    if (now - last) >= threshold:
        fetched = _fetch_latest_tag()
        # Advance the throttle clock on every *attempt* — success or not — so
        # a persistent outage or a 403 rate-limit can't defeat HARD_FLOOR and
        # let a restart loop hammer the API. ``latest`` stays sticky: it's
        # only overwritten when a fetch actually succeeds.
        cache["checked_at"] = now
        if fetched is not None:
            latest = fetched
            cache["latest"] = latest
        _save_cache(cache)

    return UpdateInfo(
        current=current,
        latest=latest,
        available=is_newer(latest, current),
        url=RELEASES_URL,
    )


def cached() -> UpdateInfo:
    """Return the last known status from cache only — never hits the network.

    Used at startup so the UI (menu row, Settings footer) can reflect a
    previously-seen release instantly, before the first live check runs.
    """
    current = current_version()
    latest = _load_cache().get("latest")
    return UpdateInfo(
        current=current,
        latest=latest,
        available=is_newer(latest, current),
        url=RELEASES_URL,
    )


def was_notified(version: str | None) -> bool:
    """True if we've already shown a desktop notification for ``version``."""
    return bool(version) and _load_cache().get("notified") == version


def mark_notified(version: str | None) -> None:
    if not version:
        return
    cache = _load_cache()
    cache["notified"] = version
    _save_cache(cache)
