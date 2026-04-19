#!/usr/bin/python3
"""Ubuntu AppIndicator for Claude Code usage (5h session + 7-day weekly).

Uses the system Python (``/usr/bin/python3``) explicitly because PyGObject
(``gi``) is provided by the Ubuntu ``python3-gi`` package and is typically
not available inside project virtualenvs.

Polls ``https://api.anthropic.com/api/oauth/usage`` (the undocumented
endpoint Claude Code's ``/usage`` command uses) and surfaces the data in
the GNOME top bar:

  * label:   "5h 17%  ·  7j 5%", prefixed with "/!\\" when an alert is active
  * menu:    session + weekly + sonnet + extra, account, refresh, quit,
             edit settings, clear active alerts
  * notifs:  on reset (new window), on crossing 80/95%, and on custom
             rate alerts defined in settings.json

UI language is FR by default. ``CLAUDE_USAGE_LANG=en`` or ``lang: "en"``
in ``~/.config/claude-usage-indicator/settings.json`` switches to English.

Deps:
    Run ``./install.sh`` — it detects Ubuntu/Debian vs Fedora/RHEL and
    installs the right packages. See README.md for the manual per-distro
    command if you prefer.

Run:
    /usr/bin/python3 claude_usage_indicator.py
"""

from __future__ import annotations

import subprocess
import sys
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
gi.require_version("Notify", "0.7")
from gi.repository import AyatanaAppIndicator3 as AppIndicator  # noqa: E402
from gi.repository import GLib, Gtk, Notify  # noqa: E402

from alerts import History, evaluate_alerts  # noqa: E402
from api import fetch_usage, parse_iso, read_account, read_token  # noqa: E402
from settings import (  # noqa: E402
    SETTINGS_PATH,
    Settings,
    load_settings,
    mtime as settings_mtime,
)
from strings import current_lang, detect_lang, set_lang, setup_locale, t  # noqa: E402

POLL_SECONDS = 60  # fallback when settings unavailable
SETTINGS_URL = "https://claude.ai/settings/usage"
APP_ID = "claude-usage-indicator"
# Snapshot the history to disk every Nth tick (5 * 60s = 5 min by default).
HISTORY_SNAPSHOT_EVERY = 5
# Suppress alerts for this long after boot — history needs to build up.
BOOT_COOLDOWN = timedelta(minutes=5)
# Exponential backoff when the endpoint returns 429 (known Anthropic-side
# bug — see claude-code issue #31021). Retries happen further and further
# apart, capped at the last value.
BACKOFF_STAGES = (120, 300, 900, 1800, 3600)

_REPO_ICONS = Path(__file__).resolve().parent / "icons"
_XDG_ICONS = Path.home() / ".local/share/claude-usage-indicator"
FALLBACK_ICON = "dialog-information-symbolic"


def _icon_path(name: str) -> Path:
    # Per-call resolve so a user dropping files in the XDG dir is picked
    # up on the next tick without a restart.
    xdg = _XDG_ICONS / name
    return xdg if xdg.exists() else _REPO_ICONS / name


def pick_icon(f_util: float, s_util: float) -> str:
    """Return the icon path for the current usage state."""
    full = _icon_path("full-claude.png")
    gray = _icon_path("gray-claude.png")
    default = _icon_path("claude.png")
    if full.exists() and (f_util >= 100 or s_util >= 100):
        return str(full)
    if gray.exists() and f_util == 0:
        return str(gray)
    if default.exists():
        return str(default)
    return FALLBACK_ICON


def _gray_icon() -> str:
    gray = _icon_path("gray-claude.png")
    return str(gray) if gray.exists() else FALLBACK_ICON


def spawn_claude_login() -> bool:
    """Open a terminal running ``claude`` to trigger OAuth login."""
    candidates = (
        ["gnome-terminal", "--", "claude"],
        ["konsole", "-e", "claude"],
        ["xfce4-terminal", "-e", "claude"],
        ["x-terminal-emulator", "-e", "claude"],
        ["xterm", "-e", "claude"],
    )
    for cmd in candidates:
        try:
            subprocess.Popen(cmd, start_new_session=True)
            return True
        except FileNotFoundError:
            continue
    return False


def format_remaining(reset: datetime | None) -> str:
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
    if not reset:
        return t("dash")
    return reset.astimezone().strftime(t("date_format"))


def progress_bar(util: float, width: int = 10) -> str:
    filled = max(0, min(width, int(round(util / 100 * width))))
    return "\u2588" * filled + "\u2591" * (width - filled)


def _fmt_secs(secs: int) -> str:
    if secs >= 3600:
        return t("duration_hours_minutes", h=secs // 3600, m=(secs % 3600) // 60)
    if secs >= 60:
        return t("duration_minutes", m=secs // 60)
    return t("duration_seconds", s=secs)


class Indicator:
    def __init__(self) -> None:
        self.settings: Settings = load_settings()
        self.settings_mtime: float = settings_mtime()
        # Env var > settings > system locale.
        lang = detect_lang(self.settings.lang)
        set_lang(lang)
        setup_locale(lang)

        self.token = read_token()
        self.prev_util: dict[str, float] = {}
        self.seen_thresholds: dict[str, set[int]] = {
            "five_hour": set(),
            "seven_day": set(),
        }
        self.last_good: dict | None = None
        self.backoff_idx: int = 0
        self.backoff_until: datetime | None = None
        self.current_icon: str | None = None

        self.history = History()
        self.history.load_from_disk()
        self.tick_counter: int = 0
        self.alert_last_fired: dict[str, datetime] = {}
        self.active_alerts: dict[str, str] = {}  # id -> label
        self.boot_cooldown_until: datetime = (
            datetime.now(timezone.utc) + BOOT_COOLDOWN
        )

        self.ind = AppIndicator.Indicator.new(
            APP_ID,
            pick_icon(0, 0),
            AppIndicator.IndicatorCategory.APPLICATION_STATUS,
        )
        self.ind.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.ind.set_label(t("label_placeholder"), t("label_guide"))

        self._build_menu()
        self._set_connected(self.token is not None)
        self.ind.set_menu(self.menu)
        Notify.init(APP_ID)

    # ---------------------------------------------------------------- menu

    def _build_menu(self) -> None:
        self.menu = Gtk.Menu()

        self.item_account = Gtk.MenuItem(label=read_account())
        self.item_account.set_tooltip_text(
            t("open_settings_tip", url=SETTINGS_URL)
        )
        self.item_account.connect(
            "activate", lambda _i: webbrowser.open(SETTINGS_URL)
        )

        self.item_login = Gtk.MenuItem(label=t("login_item"))
        self.item_login.connect("activate", self._on_login_clicked)

        self.item_session = self._info_item(t("session_placeholder"))
        self.item_session_reset = self._info_item(t("reset_arrow_placeholder"))
        self.item_weekly = self._info_item(t("weekly_placeholder"))
        self.item_weekly_reset = self._info_item(t("reset_arrow_placeholder"))
        self.item_sonnet = self._info_item(t("sonnet_none"))
        self.item_extra = self._info_item(t("extra_placeholder"))

        self.item_active_alerts = self._info_item("")
        self.item_clear_alerts = Gtk.MenuItem(label=t("clear_alerts"))
        self.item_clear_alerts.connect("activate", self._on_clear_alerts)

        self.item_refresh = Gtk.MenuItem(label=t("refresh_never"))
        self.item_refresh.connect("activate", self._on_refresh_clicked)
        self.item_edit_settings = Gtk.MenuItem(label=t("edit_settings"))
        self.item_edit_settings.connect("activate", self._on_edit_settings)
        quit_item = Gtk.MenuItem(label=t("quit"))
        quit_item.connect("activate", lambda _i: Gtk.main_quit())

        sep_account = Gtk.SeparatorMenuItem()
        sep_session = Gtk.SeparatorMenuItem()
        sep_weekly = Gtk.SeparatorMenuItem()
        self.sep_alerts = Gtk.SeparatorMenuItem()
        sep_refresh = Gtk.SeparatorMenuItem()
        sep_settings = Gtk.SeparatorMenuItem()
        sep_quit = Gtk.SeparatorMenuItem()

        for item in (
            self.item_account,
            sep_account,
            self.item_login,
            self.item_session,
            self.item_session_reset,
            sep_session,
            self.item_weekly,
            self.item_weekly_reset,
            sep_weekly,
            self.item_sonnet,
            self.item_extra,
            self.sep_alerts,
            self.item_active_alerts,
            self.item_clear_alerts,
            sep_refresh,
            self.item_refresh,
            sep_settings,
            self.item_edit_settings,
            sep_quit,
            quit_item,
        ):
            self.menu.append(item)

        self._connected_only = (
            self.item_session,
            self.item_session_reset,
            sep_session,
            self.item_weekly,
            self.item_weekly_reset,
            sep_weekly,
            self.item_sonnet,
            self.item_extra,
            sep_refresh,
            self.item_refresh,
        )
        self._disconnected_only = (self.item_login,)
        self._alert_only = (
            self.sep_alerts,
            self.item_active_alerts,
            self.item_clear_alerts,
        )
        self.menu.show_all()
        # Alert block is hidden until an alert fires.
        for item in self._alert_only:
            item.hide()

    @staticmethod
    def _info_item(label: str) -> Gtk.MenuItem:
        item = Gtk.MenuItem(label=label)
        item.set_sensitive(False)
        return item

    # -------------------------------------------------------------- helpers

    def notify(self, summary: str, body: str, urgent: bool = False) -> None:
        n = Notify.Notification.new(summary, body, "dialog-information")
        n.set_urgency(
            Notify.Urgency.CRITICAL if urgent else Notify.Urgency.NORMAL
        )
        try:
            n.show()
        except GLib.Error as e:
            print(f"notify failed: {e}", file=sys.stderr)

    def _set_connected(self, connected: bool) -> None:
        for item in self._connected_only:
            (item.show if connected else item.hide)()
        for item in self._disconnected_only:
            (item.hide if connected else item.show)()
        self.item_account.set_label(
            read_account() if connected else t("not_connected")
        )

    def _refresh_alert_menu(self) -> None:
        if not self.active_alerts:
            for item in self._alert_only:
                item.hide()
            return
        labels = "  ·  ".join(self.active_alerts.values())
        self.item_active_alerts.set_label(labels)
        for item in self._alert_only:
            item.show()

    def _on_login_clicked(self, _item: Gtk.MenuItem) -> None:
        if spawn_claude_login():
            self.notify(t("login_title_ok"), t("login_body_ok"))
        else:
            self.notify(t("login_title_fail"), t("login_body_fail"))

    def _on_clear_alerts(self, _item: Gtk.MenuItem) -> None:
        self.active_alerts.clear()
        self._refresh_alert_menu()

    def _on_edit_settings(self, _item: Gtk.MenuItem) -> None:
        try:
            subprocess.Popen(
                ["xdg-open", str(SETTINGS_PATH)], start_new_session=True
            )
        except FileNotFoundError:
            pass

    def _show_rate_limited(self, wait_secs: int) -> None:
        """Keep the previous stats on screen, switch icon to gray."""
        if self.last_good is not None:
            self._render(self.last_good, fresh=False)
        icon = _gray_icon()
        if icon != self.current_icon:
            self.ind.set_icon_full(icon, "Claude usage — rate-limited")
            self.current_icon = icon
        self.item_refresh.set_label(
            t("rate_limited", d=_fmt_secs(wait_secs))
        )

    def _on_refresh_clicked(self, _item: Gtk.MenuItem) -> None:
        self.item_refresh.set_label(t("refresh_pending"))
        GLib.idle_add(self._deferred_tick)

    def _deferred_tick(self) -> bool:
        self.tick()
        return False  # one-shot idle source

    # ---------------------------------------------------------------- tick

    def _reload_settings_if_needed(self) -> None:
        current = settings_mtime()
        if current and current != self.settings_mtime:
            old_lang = self.settings.lang
            self.settings = load_settings()
            self.settings_mtime = current
            new_lang = detect_lang(self.settings.lang)
            if new_lang != current_lang():
                set_lang(new_lang)
                setup_locale(new_lang)
                # Relabel static items; dynamic ones refresh on next render.
                self.item_login.set_label(t("login_item"))
                self.item_clear_alerts.set_label(t("clear_alerts"))
                self.item_edit_settings.set_label(t("edit_settings"))
            self.notify(
                t("settings_reloaded_title"),
                t("settings_reloaded_body"),
            )
            _ = old_lang  # kept for possible future lang-change handling

    def tick(self) -> bool:
        self._reload_settings_if_needed()
        # Re-check on every tick — user may log in/out while daemon runs.
        self.token = read_token()
        if not self.token:
            self._set_connected(False)
            self.ind.set_label(t("label_not_connected"), t("label_not_connected"))
            icon = _gray_icon()
            if icon != self.current_icon:
                self.ind.set_icon_full(icon, "Claude usage")
                self.current_icon = icon
            return True

        self._set_connected(True)
        now = datetime.now(timezone.utc)
        if self.backoff_until and now < self.backoff_until:
            remaining = int((self.backoff_until - now).total_seconds())
            self._show_rate_limited(remaining)
            return True

        data = fetch_usage(self.token)
        if data.get("rate_limited"):
            default_wait = BACKOFF_STAGES[
                min(self.backoff_idx, len(BACKOFF_STAGES) - 1)
            ]
            wait = int(data.get("retry_after") or default_wait)
            self.backoff_until = now + timedelta(seconds=wait)
            self.backoff_idx = min(
                self.backoff_idx + 1, len(BACKOFF_STAGES) - 1
            )
            self._show_rate_limited(wait)
            return True
        if "error" in data:
            self.ind.set_label(t("label_error"), "")
            self.item_session.set_label(t("error_line", msg=data["error"]))
            self.item_refresh.set_label(
                t("refresh_fail", ts=now.astimezone().strftime("%H:%M:%S"))
            )
            return True

        # Success: reset backoff state.
        self.backoff_idx = 0
        self.backoff_until = None
        self.last_good = data
        self._render(data, now=now)
        return True

    # -------------------------------------------------------------- render

    def _render(
        self, data: dict, fresh: bool = True, now: datetime | None = None
    ) -> None:
        """Render usage stats to the menu and top bar."""
        now = now or datetime.now(timezone.utc)
        five = data.get("five_hour") or {}
        seven = data.get("seven_day") or {}
        sonnet = data.get("seven_day_sonnet") or {}
        extra = data.get("extra_usage") or {}

        f_util = float(five.get("utilization") or 0)
        s_util = float(seven.get("utilization") or 0)
        so_util = float(sonnet.get("utilization") or 0)

        f_reset = parse_iso(five.get("resets_at"))
        s_reset = parse_iso(seven.get("resets_at"))

        if fresh:
            reset_metrics = self._detect_resets(f_util, s_util, so_util)
            self._check_thresholds("five_hour", t("session_5h"), f_util, f_reset)
            self._check_thresholds("seven_day", t("weekly_7d"), s_util, s_reset)

            self.history.append("five_hour", f_util, now)
            self.history.append("seven_day", s_util, now)
            if sonnet.get("resets_at") or so_util:
                self.history.append("seven_day_sonnet", so_util, now)

            fired = evaluate_alerts(
                self.settings.alerts,
                self.history,
                self.alert_last_fired,
                reset_metrics,
                now,
                self.boot_cooldown_until,
            )
            for f in fired:
                self.notify(
                    t("alert_fired_title", label=f.label),
                    t(
                        "alert_fired_body",
                        delta=f.delta,
                        window=f.window_hours,
                        metric=f.metric,
                    ),
                    urgent=True,
                )
                self.active_alerts[f.alert_id] = f.label
            # Any metric that reset drops its active alert entry.
            if reset_metrics:
                to_drop = [
                    aid
                    for aid, _ in list(self.active_alerts.items())
                    for a in self.settings.alerts
                    if a.id == aid and a.metric in reset_metrics
                ]
                for aid in to_drop:
                    self.active_alerts.pop(aid, None)
            self._refresh_alert_menu()

            self.tick_counter += 1
            if self.tick_counter % HISTORY_SNAPSHOT_EVERY == 0:
                self.history.snapshot_to_disk()

        label_key = "label_main_alert" if self.active_alerts else "label_main"
        self.ind.set_label(
            t(label_key, f=f_util, s=s_util),
            t("label_guide"),
        )

        if fresh:
            icon = pick_icon(f_util, s_util)
            if icon != self.current_icon:
                self.ind.set_icon_full(icon, "Claude usage")
                self.current_icon = icon

        self.item_session.set_label(
            t("session_line", bar=progress_bar(f_util), util=f_util)
        )
        self.item_session_reset.set_label(
            t(
                "reset_line",
                rem=format_remaining(f_reset),
                abs=format_local(f_reset),
            )
        )
        self.item_weekly.set_label(
            t("weekly_line", bar=progress_bar(s_util), util=s_util)
        )
        self.item_weekly_reset.set_label(
            t(
                "reset_line",
                rem=format_remaining(s_reset),
                abs=format_local(s_reset),
            )
        )
        if sonnet.get("resets_at") or so_util:
            self.item_sonnet.set_label(
                t("sonnet_line", bar=progress_bar(so_util), util=so_util)
            )
        else:
            self.item_sonnet.set_label(t("sonnet_none"))

        if extra.get("is_enabled"):
            used = extra.get("used_credits") or 0
            limit = extra.get("monthly_limit") or 0
            currency = extra.get("currency") or ""
            self.item_extra.set_label(
                t(
                    "extra_active",
                    used=used,
                    limit=limit,
                    currency=currency,
                ).strip()
            )
        else:
            self.item_extra.set_label(t("extra_disabled"))

        self.item_refresh.set_label(
            t("refresh_ts", ts=datetime.now().strftime("%H:%M:%S"))
        )

    def _detect_resets(
        self, f_util: float, s_util: float, so_util: float
    ) -> set[str]:
        """Detect resets by utilisation drop >5 pp. Mutate prev_util."""
        reset_metrics: set[str] = set()
        for metric, util, label in (
            ("five_hour", f_util, t("session_5h")),
            ("seven_day", s_util, t("weekly_7d")),
            ("seven_day_sonnet", so_util, t("sonnet_7d")),
        ):
            prev = self.prev_util.get(metric)
            if prev is not None and util + 5 < prev:
                self.notify(
                    t("reset_title", label=label),
                    t("reset_body"),
                )
                self.seen_thresholds.get(metric, set()).clear()
                reset_metrics.add(metric)
            self.prev_util[metric] = util
        return reset_metrics

    def _check_thresholds(
        self,
        metric: str,
        label: str,
        util: float,
        reset: datetime | None,
    ) -> None:
        seen = self.seen_thresholds.get(metric)
        if seen is None:
            return
        for threshold in self.settings.builtin_thresholds:
            if util >= threshold and threshold not in seen:
                seen.add(threshold)
                self.notify(
                    t("threshold_title", label=label, util=util),
                    t(
                        "threshold_body",
                        abs=format_local(reset),
                        rem=format_remaining(reset),
                    ),
                    urgent=threshold >= 95,
                )


def main() -> int:
    indicator = Indicator()
    indicator.tick()
    # Honour the configured poll cadence.
    GLib.timeout_add_seconds(indicator.settings.poll_seconds, indicator.tick)
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
