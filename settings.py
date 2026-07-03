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

VALID_METRICS = (
    "five_hour",
    "seven_day",
    "seven_day_sonnet",
)

# Metrics that can appear in the top-bar label. A subset is user-selectable
# in the settings dialog; the dropdown menu always shows the full detail
# regardless of these.
VALID_CLAUDE_TOPBAR_METRICS = ("five_hour", "seven_day", "seven_day_sonnet")
DEFAULT_CLAUDE_TOPBAR_METRICS = ("five_hour", "seven_day")

# v2 introduced the ``topbar`` block. v1 files load fine — the new keys
# default gracefully via ``.get(...)``.
SCHEMA_VERSION = 2


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
class TopbarSettings:
    """How the GNOME top-bar label is composed.

    ``show_claude`` gates the whole label segment; ``claude_metrics`` picks
    which values appear inside it. An empty metric tuple hides the segment
    even when ``show_claude`` is True (a deliberate, honoured state — not
    coerced back to the default). Separators are kept ASCII-safe because the
    top-bar font drops most non-ASCII glyphs.
    """

    show_claude: bool = True
    claude_metrics: tuple[str, ...] = DEFAULT_CLAUDE_TOPBAR_METRICS
    show_provider_prefix: bool = True
    show_alert_prefix: bool = True
    # Prefix each value with its short window label ("5h 42% . 7j 78%")
    # instead of bare percentages. Pairs well with show_provider_prefix off.
    metric_labels: bool = False
    compact: bool = False
    metric_separator: str = " . "
    percent_decimals: int = 0


@dataclass(frozen=True)
class Settings:
    schema_version: int = SCHEMA_VERSION
    lang: str = "en"
    poll_seconds: int = 120
    builtin_thresholds: tuple[int, ...] = (80, 95)
    # Multi-account: capture every account you sign in as and show their
    # usage in the menu. ``account_switch_enabled`` additionally allows the
    # (invasive) switch that rewrites ~/.claude — off by default.
    accounts_enabled: bool = True
    account_switch_enabled: bool = False
    # Check GitHub for a newer release (unauthenticated GET, nothing sent).
    update_check_enabled: bool = True
    topbar: TopbarSettings = field(default_factory=TopbarSettings)
    alerts: tuple[AlertDef, ...] = field(default_factory=tuple)


DEFAULT_SETTINGS_JSON: dict[str, Any] = {
    "schema_version": SCHEMA_VERSION,
    "lang": "en",
    "poll_seconds": 120,
    "builtin_thresholds": [80, 95],
    "accounts_enabled": True,
    "account_switch_enabled": False,
    "update_check_enabled": True,
    "topbar": {
        "show_claude": True,
        "claude_metrics": list(DEFAULT_CLAUDE_TOPBAR_METRICS),
        "show_provider_prefix": True,
        "show_alert_prefix": True,
        "metric_labels": False,
        "compact": False,
        "metric_separator": " . ",
        "percent_decimals": 0,
    },
    "alerts": [
        {
            "id": "daily-burn",
            "enabled": False,
            "metric": "seven_day",
            "delta_pp": 20,
            "window_hours": 12,
            "cooldown_hours": 6,
            "label": "Fast weekly burn",
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


def _coerce_bool(v: Any, default: bool) -> bool:
    return v if isinstance(v, bool) else default


# A tiny allowlist of non-ASCII separators that render reliably in the GNOME
# top-bar font. The blanket "ASCII only" rule exists to avoid missing glyphs
# (we hit this with the calendar emoji 🗓); these Latin-1/General-Punctuation
# characters are near-universally present, so they're safe to permit.
_SEPARATOR_EXTRAS = "·•–—"  # · • – —


def sanitize_separator(v: Any, default: str, *, max_len: int = 8) -> str:
    """Keep printable ASCII + a few safe separator glyphs; else the default.

    The top-bar font drops most non-ASCII glyphs, so separators are limited
    to ``0x20..0x7e`` plus :data:`_SEPARATOR_EXTRAS` (``· • – —``). Public so
    the settings dialog can mirror it in its live preview.
    """
    if not isinstance(v, str):
        return default
    kept = "".join(
        ch for ch in v if 0x20 <= ord(ch) <= 0x7E or ch in _SEPARATOR_EXTRAS
    )
    if not kept:
        return default
    return kept[:max_len]


def _coerce_metrics(
    v: Any, valid: tuple[str, ...], default: tuple[str, ...]
) -> tuple[str, ...]:
    """Filter a metric list to valid ids, preserving order and de-duping.

    A missing key (``None``) yields the default. An explicit empty/invalid
    list yields an empty tuple — the user chose to show nothing there.
    """
    if v is None:
        return default
    if not isinstance(v, list):
        return default
    seen: set[str] = set()
    out: list[str] = []
    for item in v:
        if item in valid and item not in seen:
            seen.add(item)
            out.append(item)
    return tuple(out)


def _validate_topbar(raw: Any) -> TopbarSettings:
    if not isinstance(raw, dict):
        return TopbarSettings()
    decimals = _coerce_int(raw.get("percent_decimals"), 0, minimum=0)
    if decimals > 2:
        decimals = 2
    return TopbarSettings(
        show_claude=_coerce_bool(raw.get("show_claude"), True),
        claude_metrics=_coerce_metrics(
            raw.get("claude_metrics"),
            VALID_CLAUDE_TOPBAR_METRICS,
            DEFAULT_CLAUDE_TOPBAR_METRICS,
        ),
        show_provider_prefix=_coerce_bool(raw.get("show_provider_prefix"), True),
        show_alert_prefix=_coerce_bool(raw.get("show_alert_prefix"), True),
        metric_labels=_coerce_bool(raw.get("metric_labels"), False),
        compact=_coerce_bool(raw.get("compact"), False),
        metric_separator=sanitize_separator(raw.get("metric_separator"), " . "),
        percent_decimals=decimals,
    )


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
    lang = str(raw.get("lang") or "en")
    poll_seconds = _coerce_int(raw.get("poll_seconds"), 120, minimum=10)

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
        accounts_enabled=_coerce_bool(raw.get("accounts_enabled"), True),
        account_switch_enabled=_coerce_bool(
            raw.get("account_switch_enabled"), False
        ),
        update_check_enabled=_coerce_bool(raw.get("update_check_enabled"), True),
        topbar=_validate_topbar(raw.get("topbar")),
        alerts=tuple(alerts),
    )


# --------------------------------------------------------------- serialization


def alert_to_dict(a: AlertDef) -> dict[str, Any]:
    return {
        "id": a.id,
        "enabled": a.enabled,
        "metric": a.metric,
        "delta_pp": a.delta_pp,
        "window_hours": a.window_hours,
        "cooldown_hours": a.cooldown_hours,
        "label": a.label,
    }


def topbar_to_dict(tb: TopbarSettings) -> dict[str, Any]:
    return {
        "show_claude": tb.show_claude,
        "claude_metrics": list(tb.claude_metrics),
        "show_provider_prefix": tb.show_provider_prefix,
        "show_alert_prefix": tb.show_alert_prefix,
        "metric_labels": tb.metric_labels,
        "compact": tb.compact,
        "metric_separator": tb.metric_separator,
        "percent_decimals": tb.percent_decimals,
    }


def settings_to_dict(s: Settings) -> dict[str, Any]:
    """Serialize a :class:`Settings` back to the on-disk JSON shape."""
    return {
        "schema_version": SCHEMA_VERSION,
        "lang": s.lang,
        "poll_seconds": s.poll_seconds,
        "builtin_thresholds": list(s.builtin_thresholds),
        "accounts_enabled": s.accounts_enabled,
        "account_switch_enabled": s.account_switch_enabled,
        "update_check_enabled": s.update_check_enabled,
        "topbar": topbar_to_dict(s.topbar),
        "alerts": [alert_to_dict(a) for a in s.alerts],
    }


def save_settings(s: Settings) -> None:
    """Atomically-ish write settings to disk. Raises ``OSError`` on failure.

    Callers (the settings dialog) catch the error and surface it to the
    user — we never swallow it silently here.
    """
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings_to_dict(s), indent=2) + "\n")
