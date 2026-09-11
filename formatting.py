"""Pure, UI-agnostic rendering helpers shared by both front-ends.

The Linux (GTK/AppIndicator) and macOS (rumps/menu-bar) apps both need to
turn raw usage numbers into human strings — durations and progress bars.
Keeping these here (no GTK, no AppKit) lets both platforms import the exact
same logic instead of duplicating it. Depends only on ``strings`` (a leaf),
so it stays portable.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from strings import t


def to_float(value: object, default: float = 0.0) -> float:
    """Coerce an untrusted API value to float, defaulting instead of raising.

    The usage endpoint is undocumented: a utilisation may arrive as a string,
    as null, or not at all. Every read of one goes through here, because a
    ``ValueError`` out of a menu render would take the whole poll loop down —
    the exact failure mode rule 3 in CLAUDE.md exists to prevent.

    NaN and the infinities are rejected too, not just unparseable values.
    Python's ``json.loads`` accepts the bare ``NaN`` token by default, and a
    NaN utilisation survives ``float()`` only to explode further downstream
    where the number meets ``int()`` — in the progress bar, or in the digits
    drawn on the Windows tray icon.
    """
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


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


def format_account_usage(
    five: float,
    seven: float,
    five_reset: datetime | None = None,
    seven_reset: datetime | None = None,
) -> str:
    """One account's row body: both windows, and when each frees up.

    A percentage alone does not answer the question you actually have in
    front of the accounts menu, which is "can I work on this one, and if not,
    when". Shared by the three front-ends because they had three identical
    copies of the percentage-only version, which is how they drift.

    Falls back to the bare percentages when the payload carried no reset
    time at all — a row of dashes would be noise, not information.
    """
    if five_reset is None and seven_reset is None:
        return t("acct_usage", five=five, seven=seven)
    return t(
        "acct_usage_reset",
        five=five,
        seven=seven,
        fr=format_remaining(five_reset),
        sr=format_remaining(seven_reset),
    )


def format_switch_confirm(email: str, count: int, known: bool = True) -> str:
    """The single question a switch asks, plus what it is about to move.

    Non-negotiable 8 wants the running-session count in front of the user
    before ``accounts.switch_to``. It rides inside this body rather than in
    a dialog of its own: open terminals following the credentials file is
    the feature, not a hazard, so it is worth a sentence and not a second
    click.

    ``known=False`` means the probe could not run, and is worded for that
    uncertainty instead of being read as "nothing is open".
    """
    body = t("acct_switch_confirm_body", email=email)
    if count > 0:
        return f"{body}\n\n{t('ho_busy_body', n=count)}"
    if not known:
        return f"{body}\n\n{t('ho_unknown_body')}"
    return body


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
