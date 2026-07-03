"""Top-bar label composition, driven by :class:`settings.TopbarSettings`.

Pure functions only — no GTK, no ``self``. The indicator calls
:func:`compose_label` each tick; the settings dialog calls the same function
on synthetic sample data to render a live preview. Keeping it here keeps the
indicator file small and the rendering logic testable in isolation.

Status segments (rate-limited / error) still come from ``strings.py`` so they
stay translatable; only the "ok" segment is built dynamically from the
selected metrics.
"""

from __future__ import annotations

from settings import TopbarSettings
from strings import t

# Provider prefix is a symbol, not a translatable word — kept ASCII so the
# top-bar font renders it (see the no-emoji rule in CLAUDE.md).
CLAUDE_PREFIX = "C"

# Where each top-bar metric lives inside the Claude data payload.
_CLAUDE_METRIC_PATHS: dict[str, tuple[str, str]] = {
    "five_hour": ("five_hour", "utilization"),
    "seven_day": ("seven_day", "utilization"),
    "seven_day_sonnet": ("seven_day_sonnet", "utilization"),
}
# Short per-metric label shown when ``metric_labels`` is on ("5h", "7j", "S7").
_CLAUDE_METRIC_LABEL_KEYS: dict[str, str] = {
    "five_hour": "session_5h",
    "seven_day": "weekly_7d",
    "seven_day_sonnet": "sonnet_7d",
}


def _to_float(value: object) -> float:
    """Coerce an untrusted API value to float, defaulting to 0.

    The usage endpoint is undocumented; a field may arrive as a string,
    null, or be missing entirely. We never let that raise — a bad value is
    just 0% rather than a crash that would kill the poll timer.
    """
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _pct(value: float, decimals: int) -> str:
    return f"{value:.{decimals}f}%"


def _claude_pairs(
    data: dict, metrics: tuple[str, ...]
) -> list[tuple[str, float]]:
    """Return ``(short_label, value)`` pairs for the selected Claude metrics."""
    out: list[tuple[str, float]] = []
    for metric in metrics:
        path = _CLAUDE_METRIC_PATHS.get(metric)
        if path is None:
            continue
        node = data.get(path[0]) or {}
        label = t(_CLAUDE_METRIC_LABEL_KEYS.get(metric, ""))
        out.append((label, _to_float(node.get(path[1]))))
    return out


def _ok_segment(
    prefix: str, pairs: list[tuple[str, float]], tb: TopbarSettings
) -> str | None:
    """Build the value part of a segment, or None when nothing is selected.

    With ``metric_labels`` on, each value is prefixed by its short window
    label ("5h 42%"); otherwise it's a bare percentage ("42%").
    """
    if not pairs:
        return None
    shown = [max(pairs, key=lambda p: p[1])] if tb.compact else pairs

    def fmt(label: str, value: float) -> str:
        pct = _pct(value, tb.percent_decimals)
        if tb.metric_labels and label:
            return f"{label} {pct}"
        return pct

    body = tb.metric_separator.join(fmt(label, v) for label, v in shown)
    if tb.show_provider_prefix:
        return f"{prefix} {body}" if body else prefix
    return body


def claude_segment(state: dict | None, tb: TopbarSettings) -> str | None:
    if state is None or state.get("status") == "absent":
        return None
    status = state.get("status")
    if status == "rate_limited":
        return t("label_seg_claude_rl")
    if status == "error":
        return t("label_seg_claude_err")
    data = state.get("data") or {}
    return _ok_segment(CLAUDE_PREFIX, _claude_pairs(data, tb.claude_metrics), tb)


def compose_label(
    claude_state: dict | None,
    tb: TopbarSettings,
    has_alerts: bool,
) -> tuple[str | None, str]:
    """Return ``(body, guide)`` for ``Indicator.set_label``.

    ``body`` is:
      * ``None``  — Claude is not connected (caller shows "not signed in")
      * ``" "``   — connected but the user hid the segment (icon-only)
      * text      — the composed label, wrapped in spaces, with an optional
                    ``/!\\`` alert prefix.

    ``guide`` is always the widest plausible string so AppIndicator reserves
    enough width regardless of the current selection.
    """
    guide = t("label_guide")

    claude_connected = (
        claude_state is not None and claude_state.get("status") != "absent"
    )
    if not claude_connected:
        return None, guide

    claude_seg = claude_segment(claude_state, tb) if tb.show_claude else None
    if not claude_seg:
        return " ", guide

    body = claude_seg
    if has_alerts and tb.show_alert_prefix:
        body = f" {t('label_alert_prefix')} {body} "
    else:
        body = f" {body} "
    return body, guide
