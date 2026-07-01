"""Top-bar label composition, driven by :class:`settings.TopbarSettings`.

Pure functions only — no GTK, no ``self``. The indicator calls
:func:`compose_label` each tick; the settings dialog calls the same function
on synthetic sample data to render a live preview. Keeping it here keeps the
indicator file small and the rendering logic testable in isolation.

Status segments (rate-limited / error / login-expired) still come from
``strings.py`` so they stay translatable; only the "ok" segment is built
dynamically from the selected metrics.
"""

from __future__ import annotations

from settings import TopbarSettings
from strings import t

# Provider prefixes are symbols, not translatable words — kept ASCII so the
# top-bar font renders them (see the no-emoji rule in CLAUDE.md).
CLAUDE_PREFIX = "C"
CODEX_PREFIX = "X"

# Where each top-bar metric lives inside a provider's data payload.
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
_CODEX_METRIC_WINDOWS: dict[str, str] = {
    "codex_primary": "primary_window",
    "codex_secondary": "secondary_window",
}


def codex_window_label(seconds: int | None) -> str:
    """Map ``limit_window_seconds`` to a localized short label.

    Buckets to the nearest of {1h, 5h, 1d, 7d, 30d}, requiring <35% relative
    error; falls back to ``codex_window_other`` (``w?``). Pure and reused by
    both the top-bar label and the dropdown rows in the indicator.
    """
    if not seconds or seconds <= 0:
        return t("codex_window_other")
    buckets = (
        (3600, "codex_window_1h"),
        (5 * 3600, "codex_window_5h"),
        (86_400, "codex_window_1d"),
        (7 * 86_400, "codex_window_7d"),
        (30 * 86_400, "codex_window_30d"),
    )
    best_key = "codex_window_other"
    best_ratio = 0.35
    for ref, key in buckets:
        ratio = abs(seconds - ref) / ref
        if ratio < best_ratio:
            best_ratio = ratio
            best_key = key
    return t(best_key)


def _to_float(value: object) -> float:
    """Coerce an untrusted API value to float, defaulting to 0.

    The usage endpoints are undocumented; a field may arrive as a string,
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


def _codex_pairs(
    data: dict, metrics: tuple[str, ...]
) -> list[tuple[str, float]]:
    """Return ``(short_label, value)`` pairs for the selected Codex metrics."""
    rl = data.get("rate_limit") or {}
    out: list[tuple[str, float]] = []
    for metric in metrics:
        window_key = _CODEX_METRIC_WINDOWS.get(metric)
        if window_key is None:
            continue
        node = rl.get(window_key) or {}
        label = codex_window_label(node.get("limit_window_seconds"))
        out.append((label, _to_float(node.get("used_percent"))))
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


def codex_segment(state: dict | None, tb: TopbarSettings) -> str | None:
    if state is None or state.get("status") == "absent":
        return None
    status = state.get("status")
    if status == "login_expired":
        return t("label_seg_codex_login")
    if status == "rate_limited":
        return t("label_seg_codex_rl")
    if status == "error":
        return t("label_seg_codex_err")
    data = state.get("data") or {}
    return _ok_segment(CODEX_PREFIX, _codex_pairs(data, tb.codex_metrics), tb)


def compose_label(
    claude_state: dict | None,
    codex_state: dict | None,
    tb: TopbarSettings,
    has_alerts: bool,
) -> tuple[str | None, str]:
    """Return ``(body, guide)`` for ``Indicator.set_label``.

    ``body`` is:
      * ``None``  — neither provider is connected (caller shows "not signed in")
      * ``" "``   — connected but the user hid every segment (icon-only)
      * text      — the composed label, wrapped in spaces, with an optional
                    ``/!\\`` alert prefix.

    ``guide`` is always the widest plausible string so AppIndicator reserves
    enough width regardless of the current selection.
    """
    guide = t("label_guide_both")

    claude_connected = (
        claude_state is not None and claude_state.get("status") != "absent"
    )
    codex_connected = (
        codex_state is not None and codex_state.get("status") != "absent"
    )
    if not claude_connected and not codex_connected:
        return None, guide

    claude_seg = claude_segment(claude_state, tb) if tb.show_claude else None
    codex_seg = codex_segment(codex_state, tb) if tb.show_codex else None

    ordered = (
        [claude_seg, codex_seg] if tb.claude_first else [codex_seg, claude_seg]
    )
    parts = [seg for seg in ordered if seg]
    if not parts:
        return " ", guide

    body = tb.separator.join(parts)
    if has_alerts and tb.show_alert_prefix:
        body = f" {t('label_alert_prefix')} {body} "
    else:
        body = f" {body} "
    return body, guide
