"""User settings: language, poll cadence, and custom alerts.

The file lives at ``~/.config/claude-usage-indicator/settings.json`` and
is created with :data:`DEFAULT_SETTINGS_JSON` on first run. Invalid JSON
or invalid entries fall back to defaults (never crash the daemon).

Alert definition fields are loosely validated: anything wrong is dropped
with a stderr warning, and the remaining alerts keep working. The file is
the single source of truth; the dataclasses are immutable snapshots used
by the daemon.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

APP_ID = "claude-usage-indicator"
SETTINGS_DIR = Path.home() / ".config" / APP_ID
SETTINGS_PATH = SETTINGS_DIR / "settings.json"

VALID_METRICS = ("five_hour", "seven_day", "seven_day_sonnet")
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class AlertDef:
    id: str
    enabled: bool
    metric: str
    delta_pp: float
    window_hours: float
    cooldown_hours: float
    label: str


@dataclass(frozen=True)
class Settings:
    schema_version: int = SCHEMA_VERSION
    lang: str = "fr"
    poll_seconds: int = 60
    builtin_thresholds: tuple[int, ...] = (80, 95)
    alerts: tuple[AlertDef, ...] = field(default_factory=tuple)


DEFAULT_SETTINGS_JSON: dict[str, Any] = {
    "schema_version": SCHEMA_VERSION,
    "lang": "fr",
    "poll_seconds": 60,
    "builtin_thresholds": [80, 95],
    "alerts": [
        {
            "id": "daily-burn",
            "enabled": False,
            "metric": "seven_day",
            "delta_pp": 20,
            "window_hours": 12,
            "cooldown_hours": 6,
            "label": "Conso hebdo rapide",
        }
    ],
}


def ensure_settings_file() -> None:
    """Write the default file if it doesn't exist yet."""
    if SETTINGS_PATH.exists():
        return
    try:
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(json.dumps(DEFAULT_SETTINGS_JSON, indent=2) + "\n")
    except OSError as e:
        print(f"settings: cannot create {SETTINGS_PATH}: {e}", file=sys.stderr)


def mtime() -> float:
    """Return the settings file mtime, or 0 if absent."""
    try:
        return SETTINGS_PATH.stat().st_mtime
    except OSError:
        return 0.0


def load_settings() -> Settings:
    """Load the file, falling back to defaults on error."""
    ensure_settings_file()
    try:
        raw = json.loads(SETTINGS_PATH.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"settings: load failed ({e}); using defaults", file=sys.stderr)
        return _from_raw(DEFAULT_SETTINGS_JSON)
    if not isinstance(raw, dict):
        print("settings: root must be an object; using defaults", file=sys.stderr)
        return _from_raw(DEFAULT_SETTINGS_JSON)
    return _from_raw(raw)


def _coerce_int(v: Any, default: int, *, minimum: int | None = None) -> int:
    try:
        out = int(v)
    except (TypeError, ValueError):
        return default
    if minimum is not None and out < minimum:
        return default
    return out


def _coerce_float(v: Any, default: float, *, minimum: float | None = None) -> float:
    try:
        out = float(v)
    except (TypeError, ValueError):
        return default
    if minimum is not None and out < minimum:
        return default
    return out


def _validate_alert(raw: dict, index: int) -> AlertDef | None:
    if not isinstance(raw, dict):
        print(f"settings: alerts[{index}] must be an object", file=sys.stderr)
        return None
    alert_id = str(raw.get("id") or f"alert-{index}")
    metric = str(raw.get("metric") or "")
    if metric not in VALID_METRICS:
        print(
            f"settings: alerts[{index}] metric {metric!r} invalid; "
            f"expected one of {VALID_METRICS}",
            file=sys.stderr,
        )
        return None
    delta_pp = _coerce_float(raw.get("delta_pp"), -1, minimum=0.1)
    if delta_pp <= 0:
        print(f"settings: alerts[{index}] delta_pp must be > 0", file=sys.stderr)
        return None
    window_hours = _coerce_float(raw.get("window_hours"), -1, minimum=0.1)
    if window_hours <= 0:
        print(
            f"settings: alerts[{index}] window_hours must be > 0",
            file=sys.stderr,
        )
        return None
    cooldown_hours = _coerce_float(
        raw.get("cooldown_hours"), window_hours / 2, minimum=0.0
    )
    return AlertDef(
        id=alert_id,
        enabled=bool(raw.get("enabled", True)),
        metric=metric,
        delta_pp=delta_pp,
        window_hours=window_hours,
        cooldown_hours=cooldown_hours,
        label=str(raw.get("label") or alert_id),
    )


def _from_raw(raw: dict) -> Settings:
    lang = str(raw.get("lang") or "fr")
    poll_seconds = _coerce_int(raw.get("poll_seconds"), 60, minimum=10)

    thresholds_raw = raw.get("builtin_thresholds") or [80, 95]
    thresholds: list[int] = []
    if isinstance(thresholds_raw, list):
        for item in thresholds_raw:
            v = _coerce_int(item, -1, minimum=1)
            if 1 <= v <= 100:
                thresholds.append(v)
    if not thresholds:
        thresholds = [80, 95]

    alerts: list[AlertDef] = []
    seen_ids: set[str] = set()
    raw_alerts = raw.get("alerts") or []
    if isinstance(raw_alerts, list):
        for i, a in enumerate(raw_alerts):
            parsed = _validate_alert(a, i)
            if parsed is None:
                continue
            if parsed.id in seen_ids:
                print(
                    f"settings: duplicate alert id {parsed.id!r}; skipping",
                    file=sys.stderr,
                )
                continue
            seen_ids.add(parsed.id)
            alerts.append(parsed)

    return Settings(
        schema_version=_coerce_int(raw.get("schema_version"), SCHEMA_VERSION),
        lang=lang,
        poll_seconds=poll_seconds,
        builtin_thresholds=tuple(thresholds),
        alerts=tuple(alerts),
    )
