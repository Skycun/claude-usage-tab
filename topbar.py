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
_CODEX_METRIC_WINDOWS: dict[str, str] = {
    "codex_primary": "primary_window",
    "codex_secondary": "secondary_window",
}


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


def _claude_values(data: dict, metrics: tuple[str, ...]) -> list[float]:
    out: list[float] = []
    for metric in metrics:
        path = _CLAUDE_METRIC_PATHS.get(metric)
        if path is None:
            continue
        node = data.get(path[0]) or {}
        out.append(_to_float(node.get(path[1])))
    return out


def _codex_values(data: dict, metrics: tuple[str, ...]) -> list[float]:
    rl = data.get("rate_limit") or {}
    out: list[float] = []
    for metric in metrics:
        window_key = _CODEX_METRIC_WINDOWS.get(metric)
        if window_key is None:
            continue
        node = rl.get(window_key) or {}
        out.append(_to_float(node.get("used_percent")))
    return out


def _ok_segment(
    prefix: str, values: list[float], tb: TopbarSettings
) -> str | None:
    """Build the value part of a segment, or None when nothing is selected."""
    if not values:
        return None
    shown = [max(values)] if tb.compact else values
    body = tb.metric_separator.join(_pct(v, tb.percent_decimals) for v in shown)
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
    return _ok_segment(CLAUDE_PREFIX, _claude_values(data, tb.claude_metrics), tb)


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
    return _ok_segment(CODEX_PREFIX, _codex_values(data, tb.codex_metrics), tb)


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
