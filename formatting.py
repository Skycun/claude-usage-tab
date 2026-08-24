"""Pure, UI-agnostic rendering helpers shared by both front-ends.

The Linux (GTK/AppIndicator) and macOS (rumps/menu-bar) apps both need to
turn raw usage numbers into human strings — durations and progress bars.
Keeping these here (no GTK, no AppKit) lets both platforms import the exact
same logic instead of duplicating it. Depends only on ``strings`` (a leaf),
so it stays portable.
"""

from __future__ import annotations

from datetime import datetime, timezone

from strings import t


def format_remaining(reset: datetime | None) -> str:
    """Human 'time until reset' — e.g. ``3h22`` / ``2j 4h`` / ``now``."""
    if not reset:
        return t("dash")
    delta = reset - datetime.now(timezone.utc)
    secs = int(delta.total_seconds())
    if secs <= 0:
        return t("now")
    hours, rem = divmod(secs, 3600)
    minutes = rem // 60
    if hours >= 24:
        days, hours = divmod(hours, 24)
        return t("duration_days_hours", d=days, h=hours)
    if hours:
        return t("duration_hours_minutes", h=hours, m=minutes)
    return t("duration_minutes", m=minutes)


def format_local(reset: datetime | None) -> str:
    """Absolute reset time in the user's locale (for notifications)."""
    if not reset:
        return t("dash")
    return reset.astimezone().strftime(t("date_format"))


def progress_bar(util: float, width: int = 10) -> str:
    """Block-character progress bar (``████░░░░░░``)."""
    filled = max(0, min(width, int(round(util / 100 * width))))
    return "█" * filled + "░" * (width - filled)


def format_cost(amount: float) -> str:
    """USD amount for the cost rows — ``$0.42`` / ``$12.40`` / ``$1204.00``.

    ccusage reports in USD only, so the symbol is fixed rather than
    translated: it names the currency, not a word. Two decimals throughout —
    the daily figure is often cents, and a mixed precision across the three
    rows reads as a bug.
    """
    try:
        return f"${float(amount):,.2f}"
    except (TypeError, ValueError):
        return t("dash")


def format_stamp(ts: float) -> str:
    """Local wall-clock for a unix timestamp (cost 'last updated' row)."""
    if not ts:
        return t("dash")
    return datetime.fromtimestamp(ts).strftime(t("date_format"))


def fmt_secs(secs: int) -> str:
    """Short duration for a rate-limit countdown."""
    if secs >= 3600:
        return t("duration_hours_minutes", h=secs // 3600, m=(secs % 3600) // 60)
    if secs >= 60:
        return t("duration_minutes", m=secs // 60)
    return t("duration_seconds", s=secs)
