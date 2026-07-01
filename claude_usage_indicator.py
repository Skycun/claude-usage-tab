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

import accounts  # noqa: E402
from alerts import History, evaluate_alerts  # noqa: E402
from api import (  # noqa: E402
    codex_present,
    fetch_codex_usage,
    fetch_usage,
    format_provider_header,
    parse_iso,
    read_account,
    read_codex_tokens,
    read_token,
)
from settings import (  # noqa: E402
    SETTINGS_PATH,
    Settings,
    load_settings,
    mtime as settings_mtime,
)
from strings import current_lang, detect_lang, set_lang, setup_locale, t  # noqa: E402
from topbar import codex_window_label, compose_label  # noqa: E402

POLL_SECONDS = 120  # fallback when settings unavailable
SETTINGS_URL = "https://claude.ai/settings/usage"
CODEX_SETTINGS_URL = "https://chatgpt.com/codex/settings/usage"
APP_ID = "claude-usage-indicator"
# Snapshot the history to disk every Nth tick (~10 min at the default cadence).
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
    """Block-character progress bar (``\u2588\u2588\u2588\u2588\u2591\u2591\u2591\u2591\u2591\u2591``) \u2014 pre-restore look."""
    filled = max(0, min(width, int(round(util / 100 * width))))
    return "\u2588" * filled + "\u2591" * (width - filled)


def _codex_reset_dt(window: dict) -> datetime | None:
    """Pull a UTC datetime out of a Codex window dict.

    The endpoint exposes ``reset_at`` as a unix timestamp (seconds) and
    ``reset_after_seconds`` as a relative offset. We prefer the absolute
    form; fall back to ``now + reset_after_seconds`` when only the
    relative form is present.
    """
    reset_at = window.get("reset_at")
    if isinstance(reset_at, (int, float)) and reset_at > 0:
        try:
            return datetime.fromtimestamp(int(reset_at), tz=timezone.utc)
        except (OSError, ValueError, OverflowError):
            return None
    rel = window.get("reset_after_seconds")
    if isinstance(rel, (int, float)) and rel >= 0:
        return datetime.now(timezone.utc) + timedelta(seconds=int(rel))
    return None


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
            "codex_primary": set(),
            "codex_secondary": set(),
        }
        self.last_good: dict | None = None
        self.backoff_idx: int = 0
        self.backoff_until: datetime | None = None
        self.current_icon: str | None = None

        # --- Codex state (mirrors Claude's, runs independently each tick) ---
        # ``codex_enabled`` is the master switch: when off we never poll
        # Codex and hide its whole section (handled via ``codex_present``).
        self.codex_present: bool = codex_present() and self.settings.codex_enabled
        self.codex_token: str | None = None
        self.codex_account_id: str | None = None
        self.codex_last_good: dict | None = None
        self.codex_backoff_idx: int = 0
        self.codex_backoff_until: datetime | None = None
        self.codex_login_expired: bool = False
        self._codex_login_notified: bool = False
        self.codex_status: str = "absent"  # ok | rate_limited | login_expired | error | absent
        self.codex_last_error: str | None = None

        self.history = History()
        self.history.load_from_disk()
        self.tick_counter: int = 0
        self.alert_last_fired: dict[str, datetime] = {}
        self.active_alerts: dict[str, str] = {}  # id -> label
        self.boot_cooldown_until: datetime = (
            datetime.now(timezone.utc) + BOOT_COOLDOWN
        )

        # In-process settings window (None when closed) + poll timer handle
        # so the cadence can be re-registered live after a settings change.
        self._settings_window: Gtk.Window | None = None
        self._poll_source_id: int | None = None

        self.ind = AppIndicator.Indicator.new(
            APP_ID,
            pick_icon(0, 0),
            AppIndicator.IndicatorCategory.APPLICATION_STATUS,
        )
        self.ind.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.ind.set_label(t("label_placeholder"), t("label_guide_both"))

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

        # Codex account header — populated from the API response (email +
        # plan_type). Hidden until the first successful Codex tick.
        self.item_account_codex = Gtk.MenuItem(
            label=t("codex_account_placeholder")
        )
        self.item_account_codex.set_tooltip_text(
            t("open_settings_tip", url=CODEX_SETTINGS_URL)
        )
        self.item_account_codex.connect(
            "activate", lambda _i: webbrowser.open(CODEX_SETTINGS_URL)
        )

        self.item_session = self._info_item(t("session_placeholder"))
        self.item_weekly = self._info_item(t("weekly_placeholder"))
        self.item_sonnet = self._info_item(t("sonnet_placeholder"))
        self.item_extra = self._info_item("")

        # Multi-account submenu — one row per stored Claude account, rebuilt
        # each tick. Hidden until at least one account is stored.
        self.item_accounts = Gtk.MenuItem(label=t("accounts_menu"))
        self.accounts_submenu = Gtk.Menu()
        self.item_accounts.set_submenu(self.accounts_submenu)

        # Codex metric rows — hidden until ~/.codex/auth.json exists.
        self.item_codex_primary = self._info_item(
            t("codex_placeholder", win=t("codex_window_5h"))
        )
        self.item_codex_secondary = self._info_item(
            t("codex_placeholder", win=t("codex_window_7d"))
        )
        self.item_codex_credits = self._info_item("")
        self.item_codex_login = self._info_item(t("codex_login_expired"))

        self.item_active_alerts = self._info_item("")
        self.item_clear_alerts = Gtk.MenuItem(label=t("clear_alerts"))
        self.item_clear_alerts.connect("activate", self._on_clear_alerts)

        self.item_refresh = Gtk.MenuItem(label=t("refresh_never"))
        self.item_refresh.connect("activate", self._on_refresh_clicked)
        self.item_edit_settings = Gtk.MenuItem(label=t("edit_settings"))
        self.item_edit_settings.connect("activate", self._on_edit_settings)
        quit_item = Gtk.MenuItem(label=t("quit"))
        quit_item.connect("activate", lambda _i: Gtk.main_quit())

        # Provider blocks (header + metrics) separated by single dividers.
        self.sep_between_providers = Gtk.SeparatorMenuItem()
        self.sep_alerts = Gtk.SeparatorMenuItem()
        sep_actions = Gtk.SeparatorMenuItem()

        for item in (
            self.item_account,             # "Claude | email (plan)"
            self.item_login,
            self.item_session,
            self.item_weekly,
            self.item_sonnet,
            self.item_extra,
            self.item_accounts,
            self.sep_between_providers,
            self.item_account_codex,       # "Codex | email (plan)"
            self.item_codex_login,
            self.item_codex_primary,
            self.item_codex_secondary,
            self.item_codex_credits,
            self.sep_alerts,
            self.item_active_alerts,
            self.item_clear_alerts,
            sep_actions,
            self.item_refresh,
            self.item_edit_settings,
            quit_item,
        ):
            self.menu.append(item)

        self._connected_only = (
            self.item_session,
            self.item_weekly,
            self.item_sonnet,
            self.item_refresh,
        )
        self._disconnected_only = (self.item_login,)
        self._alert_only = (
            self.sep_alerts,
            self.item_active_alerts,
            self.item_clear_alerts,
        )
        # Whole Codex block; sub-items are toggled finer by _set_codex_visible.
        # ``sep_between_providers`` is part of the Codex group — when Codex is
        # absent we don't want a trailing separator after the Claude block.
        self._codex_only = (
            self.item_account_codex,
            self.sep_between_providers,
            self.item_codex_login,
            self.item_codex_primary,
            self.item_codex_secondary,
            self.item_codex_credits,
        )
        self.menu.show_all()
        # Alert block is hidden until an alert fires.
        for item in self._alert_only:
            item.hide()
        # Codex block starts hidden; toggled per-tick.
        for item in self._codex_only:
            item.hide()
        # Extra row hidden by default — only shown when extra credits enabled.
        self.item_extra.hide()
        # Accounts submenu hidden until we've stored at least one account.
        self.item_accounts.hide()

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
        if connected:
            self.item_account.set_label(read_account())
        else:
            # Keep the "Claude | …" header even when disconnected so the
            # provider label stays consistent across both sections.
            self.item_account.set_label(
                format_provider_header("Claude", None, t("not_connected"))
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
        self._open_settings_dialog()

    def _open_settings_dialog(self) -> None:
        """Open (or re-focus) the in-process GTK settings window."""
        if self._settings_window is not None:
            self._settings_window.present()
            return
        # Lazy import: keeps the GTK dialog code out of the hot path and
        # ensures ``gi`` versions are already pinned by the time it loads.
        from settings_dialog import SettingsDialog

        dlg = SettingsDialog(
            self.settings,
            on_save=self._on_settings_saved,
            on_close=self._on_settings_closed,
            on_edit_json=self._open_settings_file,
        )
        self._settings_window = dlg
        dlg.show_all()
        dlg.present()

    def _on_settings_saved(self) -> None:
        self.apply_settings_now()

    def _on_settings_closed(self) -> None:
        self._settings_window = None

    def _open_settings_file(self) -> None:
        """Open the raw JSON (for alerts and any field the GUI doesn't cover)."""
        try:
            subprocess.Popen(
                ["xdg-open", str(SETTINGS_PATH)], start_new_session=True
            )
        except FileNotFoundError:
            pass

    def _set_codex_visible(
        self,
        present: bool,
        login_expired: bool,
        has_credits: bool,
        has_account: bool,
    ) -> None:
        """Show/hide Codex menu rows based on the current state.

        ``has_account`` is True once we've successfully fetched at least
        one usage payload (so we know the account email/plan).
        """
        if not present:
            for item in self._codex_only:
                item.hide()
            return

        if has_account:
            self.item_account_codex.show()
        else:
            self.item_account_codex.hide()

        # Separator between Claude header (always present) and Codex block.
        self.sep_between_providers.show()

        if login_expired:
            self.item_codex_login.show()
            self.item_codex_primary.hide()
            self.item_codex_secondary.hide()
            self.item_codex_credits.hide()
            return

        self.item_codex_login.hide()
        self.item_codex_primary.show()
        self.item_codex_secondary.show()
        if has_credits:
            self.item_codex_credits.show()
        else:
            self.item_codex_credits.hide()

    def _on_refresh_clicked(self, _item: Gtk.MenuItem) -> None:
        self.item_refresh.set_label(t("refresh_pending"))
        GLib.idle_add(self._deferred_tick)

    def _deferred_tick(self) -> bool:
        self.tick()
        return False  # one-shot idle source

    # ---------------------------------------------------------------- tick

    def start_poll_timer(self) -> None:
        """Register the periodic tick at the configured cadence."""
        self._poll_source_id = GLib.timeout_add_seconds(
            self.settings.poll_seconds, self.tick
        )

    def _reset_poll_timer(self) -> None:
        if self._poll_source_id is not None:
            GLib.source_remove(self._poll_source_id)
            self._poll_source_id = None
        self.start_poll_timer()

    def _relabel_static(self) -> None:
        """Re-translate menu items that don't refresh on the render path."""
        self.item_login.set_label(t("login_item"))
        self.item_clear_alerts.set_label(t("clear_alerts"))
        self.item_edit_settings.set_label(t("edit_settings"))

    def _apply_loaded_settings(self, old: Settings) -> None:
        """React to a freshly loaded ``self.settings`` (lang, poll cadence)."""
        new_lang = detect_lang(self.settings.lang)
        if new_lang != current_lang():
            set_lang(new_lang)
            setup_locale(new_lang)
            self._relabel_static()
        if old.poll_seconds != self.settings.poll_seconds:
            self._reset_poll_timer()

    def _reload_settings_if_needed(self) -> None:
        """Pick up external edits to settings.json (mtime-watched)."""
        current = settings_mtime()
        if current and current != self.settings_mtime:
            old = self.settings
            self.settings = load_settings()
            self.settings_mtime = current
            self._apply_loaded_settings(old)
            self.notify(
                t("settings_reloaded_title"),
                t("settings_reloaded_body"),
            )

    def apply_settings_now(self) -> None:
        """Apply settings written by the in-process dialog, immediately.

        Updates ``settings_mtime`` so the mtime-watcher won't re-fire (and
        won't double-notify), then forces a render via a deferred tick.
        """
        old = self.settings
        # Snapshot mtime *before* loading so the stored value matches the
        # content we read — otherwise an edit landing between the two reads
        # would be silently skipped by the mtime watcher.
        new_mtime = settings_mtime()
        self.settings = load_settings()
        self.settings_mtime = new_mtime
        self._apply_loaded_settings(old)
        GLib.idle_add(self._deferred_tick)

    def tick(self) -> bool:
        """Periodic entry point — never lets an exception kill the timer.

        Returning True keeps the GLib timeout source alive. An unhandled
        exception would make the binding drop the source and freeze the
        daemon, so we treat any failed tick as transient: log it (without
        the token — see api.py) and keep polling. The undocumented usage
        endpoints can return unexpected field types; graceful degradation
        is a project non-negotiable.
        """
        try:
            self._run_tick()
        except Exception as e:  # noqa: BLE001 — last-resort daemon guard
            print(f"tick error: {type(e).__name__}: {e}", file=sys.stderr)
        return True

    def _run_tick(self) -> None:
        """Top-level orchestrator. Polls both providers, then renders once."""
        self._reload_settings_if_needed()

        # Re-check both providers each tick — user may log in/out while
        # the daemon runs, and they may install/uninstall the Codex CLI.
        # ``codex_enabled`` is the master switch: off → no polling, no UI.
        self.token = read_token()
        self.codex_present = codex_present() and self.settings.codex_enabled
        self._set_connected(self.token is not None)

        now = datetime.now(timezone.utc)
        claude_state = self._tick_claude(now) if self.token else None
        codex_state = self._tick_codex(now) if self.codex_present else None

        # Multi-account: snapshot the active account, then poll the others.
        account_states: list[dict] = []
        if self.settings.accounts_enabled:
            if self.token:
                accounts.capture(now)
            account_states = self._tick_accounts(now, claude_state)

        self._render(claude_state, codex_state, account_states, now=now)

    def _tick_claude(self, now: datetime) -> dict:
        """Fetch (or replay) Claude usage. Returns a state dict for ``_render``.

        Backoff is handled here: a 429 schedules the next retry far enough
        in the future (per ``BACKOFF_STAGES``) and intermediate ticks
        replay the last known good data instead of hitting the endpoint.
        """
        if self.backoff_until and now < self.backoff_until:
            wait = int((self.backoff_until - now).total_seconds())
            return {
                "status": "rate_limited",
                "data": self.last_good,
                "fresh": False,
                "wait_secs": max(0, wait),
                "error_msg": None,
            }

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
            return {
                "status": "rate_limited",
                "data": self.last_good,
                "fresh": False,
                "wait_secs": wait,
                "error_msg": None,
            }

        if "error" in data:
            return {
                "status": "error",
                "data": self.last_good,
                "fresh": False,
                "wait_secs": None,
                "error_msg": data["error"],
            }

        # Success: reset backoff state.
        self.backoff_idx = 0
        self.backoff_until = None
        self.last_good = data
        return {
            "status": "ok",
            "data": data,
            "fresh": True,
            "wait_secs": None,
            "error_msg": None,
        }

    def _tick_codex(self, now: datetime) -> dict:
        """Fetch (or replay) Codex usage. Returns a state dict for ``_render``.

        Mirrors :meth:`_tick_claude` but speaks to ``chatgpt.com``. Adds a
        ``login_expired`` status path triggered by HTTP 401 — re-auth
        requires running the ``codex`` CLI, so we only flag it in the UI.
        """
        access_token, account_id = read_codex_tokens()
        if not access_token or not account_id:
            self.codex_status = "absent"
            return {
                "status": "absent",
                "data": self.codex_last_good,
                "fresh": False,
                "wait_secs": None,
                "error_msg": None,
            }

        self.codex_token = access_token
        self.codex_account_id = account_id

        if self.codex_backoff_until and now < self.codex_backoff_until:
            wait = int((self.codex_backoff_until - now).total_seconds())
            self.codex_status = "rate_limited"
            return {
                "status": "rate_limited",
                "data": self.codex_last_good,
                "fresh": False,
                "wait_secs": max(0, wait),
                "error_msg": None,
            }

        data = fetch_codex_usage(access_token, account_id)

        if data.get("login_expired"):
            self.codex_login_expired = True
            self.codex_status = "login_expired"
            if not self._codex_login_notified:
                self._codex_login_notified = True
                self.notify(
                    t("codex_login_expired"),
                    t("codex_login_expired"),
                    urgent=True,
                )
            return {
                "status": "login_expired",
                "data": self.codex_last_good,
                "fresh": False,
                "wait_secs": None,
                "error_msg": "HTTP 401",
            }

        if data.get("rate_limited"):
            default_wait = BACKOFF_STAGES[
                min(self.codex_backoff_idx, len(BACKOFF_STAGES) - 1)
            ]
            wait = int(data.get("retry_after") or default_wait)
            self.codex_backoff_until = now + timedelta(seconds=wait)
            self.codex_backoff_idx = min(
                self.codex_backoff_idx + 1, len(BACKOFF_STAGES) - 1
            )
            self.codex_status = "rate_limited"
            return {
                "status": "rate_limited",
                "data": self.codex_last_good,
                "fresh": False,
                "wait_secs": wait,
                "error_msg": None,
            }

        if "error" in data:
            self.codex_status = "error"
            self.codex_last_error = data["error"]
            return {
                "status": "error",
                "data": self.codex_last_good,
                "fresh": False,
                "wait_secs": None,
                "error_msg": data["error"],
            }

        # Success: reset backoff and login flag.
        self.codex_backoff_idx = 0
        self.codex_backoff_until = None
        self.codex_login_expired = False
        self._codex_login_notified = False
        self.codex_last_good = data
        self.codex_status = "ok"
        return {
            "status": "ok",
            "data": data,
            "fresh": True,
            "wait_secs": None,
            "error_msg": None,
        }

    # -------------------------------------------------------------- render

    def _render(
        self,
        claude_state: dict | None,
        codex_state: dict | None,
        account_states: list[dict] | None = None,
        now: datetime | None = None,
    ) -> None:
        """Compose label + dropdown from both providers' states."""
        now = now or datetime.now(timezone.utc)

        # --- Per-provider section work (history append + alerts + threshold) ---
        reset_metrics: set[str] = set()
        if claude_state and claude_state["status"] == "ok" and claude_state["fresh"]:
            reset_metrics |= self._render_claude_fresh(claude_state["data"], now)
        if codex_state and codex_state["status"] == "ok" and codex_state["fresh"]:
            reset_metrics |= self._render_codex_fresh(codex_state["data"], now)

        # --- Custom alerts engine (works on history, metric-string-agnostic) ---
        any_fresh = (
            (claude_state and claude_state["fresh"])
            or (codex_state and codex_state["fresh"])
        )
        if any_fresh:
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

        # --- Compose top-bar label honouring the user's display settings ---
        body, guide = compose_label(
            claude_state,
            codex_state,
            self.settings.topbar,
            bool(self.active_alerts),
        )
        if body is None:
            self.ind.set_label(
                t("label_not_connected"), t("label_not_connected")
            )
        else:
            self.ind.set_label(body, guide)

        # --- Update icon based on max utilisation across both providers ---
        self._update_icon(claude_state, codex_state)

        # --- Update Claude dropdown rows (always, even on stale data) ---
        if claude_state is not None:
            self._render_claude_section(claude_state)

        # --- Update Codex dropdown rows + visibility ---
        codex_data = codex_state["data"] if codex_state else None
        credits = (codex_data or {}).get("credits") or {}
        # Only render the credits row when the account actually has credits
        # — Plus/Pro plans expose ``{"has_credits": false, ...}`` which is
        # non-empty but useless to show.
        has_credits = bool(credits.get("has_credits"))
        # The Codex account row is shown once we have data (email/plan come
        # from the API, not from auth.json).
        has_account = bool(codex_data)
        self._set_codex_visible(
            present=self.codex_present,
            login_expired=self.codex_login_expired,
            has_credits=has_credits,
            has_account=has_account,
        )
        if codex_state is not None and not self.codex_login_expired:
            self._render_codex_section(codex_state)

        # --- Refresh-row timestamp / rate-limited countdown ---
        self._update_refresh_label(claude_state, codex_state, any_fresh)

        # --- Multi-account submenu ---
        self._render_accounts(account_states or [])

    # -- accounts --------------------------------------------------------

    def _tick_accounts(
        self, now: datetime, claude_state: dict | None
    ) -> list[dict]:
        """Poll every stored account. The active one reuses the live Claude
        state; the rest are fetched (refreshing an expired token first)."""
        active = accounts.active_id()
        states: list[dict] = []
        for acct in accounts.list_accounts():
            if acct.id == active:
                states.append(
                    {
                        "acct": acct,
                        "active": True,
                        "status": (
                            claude_state.get("status")
                            if claude_state
                            else "error"
                        ),
                        "data": claude_state.get("data") if claude_state else None,
                    }
                )
            else:
                states.append(self._poll_stored_account(acct, now))
        return states

    def _poll_stored_account(self, acct: accounts.Account, now: datetime) -> dict:
        """Fetch one inactive account's usage, refreshing its token if needed.

        Never refreshes the *active* account (the caller handles that) so we
        don't race Claude Code's own refresh of the shared refresh token.
        """
        blob = accounts.load_blob(acct.id) or {}
        oauth = blob.get("claudeAiOauth") or {}
        token = oauth.get("accessToken")
        if not token:
            return {"acct": acct, "active": False, "status": "expired", "data": None}
        if accounts.token_expired(oauth, now):
            token = accounts.refresh(acct.id)
            if not token:
                return {
                    "acct": acct,
                    "active": False,
                    "status": "expired",
                    "data": None,
                }
        data = fetch_usage(token)
        if data.get("error") == "HTTP 401":
            # Stored token rejected — one refresh + retry before giving up.
            token = accounts.refresh(acct.id)
            if token:
                data = fetch_usage(token)
        if data.get("rate_limited"):
            return {
                "acct": acct,
                "active": False,
                "status": "rate_limited",
                "data": None,
            }
        if "error" in data:
            status = "expired" if data.get("error") == "HTTP 401" else "error"
            return {"acct": acct, "active": False, "status": status, "data": None}
        return {"acct": acct, "active": False, "status": "ok", "data": data}

    def _render_accounts(self, states: list[dict]) -> None:
        """Rebuild the accounts submenu from the polled states."""
        if not self.settings.accounts_enabled or not states:
            self.item_accounts.hide()
            return
        for child in self.accounts_submenu.get_children():
            self.accounts_submenu.remove(child)
        for st in states:
            self.accounts_submenu.append(self._build_account_item(st))
        self.accounts_submenu.show_all()
        self.item_accounts.show()

    def _account_usage_text(self, st: dict) -> str:
        status = st.get("status")
        if status == "ok" and st.get("data"):
            data = st["data"]
            five = float((data.get("five_hour") or {}).get("utilization") or 0)
            seven = float((data.get("seven_day") or {}).get("utilization") or 0)
            return t("acct_usage", five=five, seven=seven)
        if status == "rate_limited":
            return t("acct_usage_rl")
        if status in ("expired", "login_expired"):
            return t("acct_usage_expired")
        return t("acct_usage_error")

    def _build_account_item(self, st: dict) -> Gtk.MenuItem:
        acct: accounts.Account = st["acct"]
        active = bool(st.get("active"))
        mark = t("acct_active_mark") if active else t("acct_inactive_mark")
        email = acct.email or acct.label
        plan_suffix = f" ({acct.plan})" if acct.plan else ""
        item = Gtk.MenuItem(
            label=f"{mark} {email}{plan_suffix}  —  {self._account_usage_text(st)}"
        )

        sub = Gtk.Menu()
        switch_item = Gtk.MenuItem()
        if active:
            switch_item.set_label(t("acct_switch_active"))
            switch_item.set_sensitive(False)
        elif not self.settings.account_switch_enabled:
            switch_item.set_label(t("acct_switch_disabled"))
            switch_item.set_sensitive(False)
        else:
            switch_item.set_label(t("acct_switch"))
            switch_item.connect(
                "activate",
                lambda _i, aid=acct.id, em=email: self._on_switch_account(aid, em),
            )
        sub.append(switch_item)

        forget_item = Gtk.MenuItem(label=t("acct_forget"))
        if active:
            forget_item.set_sensitive(False)
        else:
            forget_item.connect(
                "activate",
                lambda _i, aid=acct.id, em=email: self._on_forget_account(aid, em),
            )
        sub.append(forget_item)

        item.set_submenu(sub)
        return item

    def _on_switch_account(self, acct_id: str, email: str) -> None:
        if not self._confirm(
            t("acct_switch_confirm_title"),
            t("acct_switch_confirm_body", email=email),
        ):
            return
        if accounts.switch_to(acct_id, datetime.now(timezone.utc)):
            self.notify(
                t("acct_switch_ok_title"), t("acct_switch_ok_body", email=email)
            )
        else:
            self.notify(
                t("acct_switch_fail_title"),
                t("acct_switch_fail_body"),
                urgent=True,
            )
        GLib.idle_add(self._deferred_tick)

    def _on_forget_account(self, acct_id: str, email: str) -> None:
        if not self._confirm(
            t("acct_forget_confirm_title"),
            t("acct_forget_confirm_body", email=email),
        ):
            return
        accounts.forget(acct_id)
        GLib.idle_add(self._deferred_tick)

    def _confirm(self, title: str, body: str) -> bool:
        dlg = Gtk.MessageDialog(
            transient_for=self._settings_window,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text=title,
        )
        dlg.format_secondary_text(body)
        resp = dlg.run()
        dlg.destroy()
        return resp == Gtk.ResponseType.OK

    # -- icon ------------------------------------------------------------

    def _update_icon(
        self, claude_state: dict | None, codex_state: dict | None
    ) -> None:
        """Pick the icon from the worst-case util across both providers."""
        # If neither provider produced fresh data this tick, leave icon alone.
        any_fresh = (
            (claude_state and claude_state.get("fresh"))
            or (codex_state and codex_state.get("fresh"))
        )
        if not any_fresh:
            return
        f_util = 0.0
        s_util = 0.0
        if claude_state and claude_state.get("data"):
            five = claude_state["data"].get("five_hour") or {}
            seven = claude_state["data"].get("seven_day") or {}
            f_util = max(f_util, float(five.get("utilization") or 0))
            s_util = max(s_util, float(seven.get("utilization") or 0))
        if codex_state and codex_state.get("data"):
            rl = codex_state["data"].get("rate_limit") or {}
            primary = rl.get("primary_window") or {}
            secondary = rl.get("secondary_window") or {}
            f_util = max(f_util, float(primary.get("used_percent") or 0))
            s_util = max(s_util, float(secondary.get("used_percent") or 0))
        icon = pick_icon(f_util, s_util)
        if icon != self.current_icon:
            self.ind.set_icon_full(icon, "Claude usage")
            self.current_icon = icon

    def _update_refresh_label(
        self,
        claude_state: dict | None,
        codex_state: dict | None,
        any_fresh: bool,
    ) -> None:
        """Pick the right text for the Refresh menu row."""
        if any_fresh:
            self.item_refresh.set_label(
                t("refresh_ts", ts=datetime.now().strftime("%H:%M:%S"))
            )
            return
        # Pick the longest active backoff to display.
        waits = []
        for state in (claude_state, codex_state):
            if state and state["status"] == "rate_limited" and state["wait_secs"]:
                waits.append(state["wait_secs"])
        if waits:
            self.item_refresh.set_label(
                t("rate_limited", d=_fmt_secs(max(waits)))
            )
            return
        # Errors / login expired: show last failure timestamp.
        for state in (claude_state, codex_state):
            if state and state["status"] in ("error", "login_expired"):
                self.item_refresh.set_label(
                    t(
                        "refresh_fail",
                        ts=datetime.now().astimezone().strftime("%H:%M:%S"),
                    )
                )
                return

    # -- per-provider dropdown rows --------------------------------------

    def _render_claude_section(self, state: dict) -> None:
        """Update Session/Weekly/Sonnet/Extra rows from the latest data.

        Single row per metric — reset time is inlined as ``↳ 3h22``.
        Sonnet stays on its own row (no reset shown — same window as 7j).
        """
        data = state.get("data") or {}
        if not data:
            return
        five = data.get("five_hour") or {}
        seven = data.get("seven_day") or {}
        sonnet = data.get("seven_day_sonnet") or {}
        extra = data.get("extra_usage") or {}

        f_util = float(five.get("utilization") or 0)
        s_util = float(seven.get("utilization") or 0)
        so_util = float(sonnet.get("utilization") or 0)
        f_reset = parse_iso(five.get("resets_at"))
        s_reset = parse_iso(seven.get("resets_at"))

        self.item_session.set_label(
            t(
                "session_line",
                bar=progress_bar(f_util),
                util=f_util,
                rem=format_remaining(f_reset),
            )
        )
        self.item_weekly.set_label(
            t(
                "weekly_line",
                bar=progress_bar(s_util),
                util=s_util,
                rem=format_remaining(s_reset),
            )
        )
        if sonnet.get("resets_at") or so_util:
            self.item_sonnet.set_label(
                t("sonnet_line", bar=progress_bar(so_util), util=so_util)
            )
            self.item_sonnet.show()
        else:
            self.item_sonnet.hide()

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
            self.item_extra.show()
        else:
            self.item_extra.hide()

    def _render_codex_section(self, state: dict) -> None:
        """Update Codex account row + window rows + credits.

        Single row per metric (reset inlined). Account row shows
        ``email · plan_type``; populated from the API response.
        """
        data = state.get("data") or {}
        if not data:
            return

        # Account row — derived from the response so we get the actual
        # ChatGPT account email even if the user has multiple.
        email = (data.get("email") or "").strip() or None
        plan_type = (data.get("plan_type") or "").strip().capitalize() or None
        if email or plan_type:
            self.item_account_codex.set_label(
                format_provider_header("Codex", email, plan_type)
            )

        rl = data.get("rate_limit") or {}
        primary = rl.get("primary_window") or {}
        secondary = rl.get("secondary_window") or {}

        p_util = float(primary.get("used_percent") or 0)
        x_util = float(secondary.get("used_percent") or 0)
        p_reset = _codex_reset_dt(primary)
        x_reset = _codex_reset_dt(secondary)
        p_win = codex_window_label(primary.get("limit_window_seconds"))
        x_win = codex_window_label(secondary.get("limit_window_seconds"))

        self.item_codex_primary.set_label(
            t(
                "codex_line",
                win=p_win,
                bar=progress_bar(p_util),
                util=p_util,
                rem=format_remaining(p_reset),
            )
        )
        self.item_codex_secondary.set_label(
            t(
                "codex_line",
                win=x_win,
                bar=progress_bar(x_util),
                util=x_util,
                rem=format_remaining(x_reset),
            )
        )

        credits = data.get("credits") or {}
        if credits.get("has_credits"):
            balance = str(credits.get("balance") or "0")
            currency = credits.get("currency") or "USD"
            self.item_codex_credits.set_label(
                t("codex_credits_balance", balance=balance, currency=currency)
            )

    # -- fresh-tick helpers (history + reset detection) ------------------

    def _render_claude_fresh(self, data: dict, now: datetime) -> set[str]:
        """Side-effects on a fresh Claude tick: history, resets, thresholds."""
        five = data.get("five_hour") or {}
        seven = data.get("seven_day") or {}
        sonnet = data.get("seven_day_sonnet") or {}
        f_util = float(five.get("utilization") or 0)
        s_util = float(seven.get("utilization") or 0)
        so_util = float(sonnet.get("utilization") or 0)
        f_reset = parse_iso(five.get("resets_at"))
        s_reset = parse_iso(seven.get("resets_at"))

        reset_metrics = self._detect_resets_for(
            (
                ("five_hour", f_util, t("session_5h")),
                ("seven_day", s_util, t("weekly_7d")),
                ("seven_day_sonnet", so_util, t("sonnet_7d")),
            )
        )
        self._check_thresholds("five_hour", t("session_5h"), f_util, f_reset)
        self._check_thresholds("seven_day", t("weekly_7d"), s_util, s_reset)

        self.history.append("five_hour", f_util, now)
        self.history.append("seven_day", s_util, now)
        if sonnet.get("resets_at") or so_util:
            self.history.append("seven_day_sonnet", so_util, now)
        return reset_metrics

    def _render_codex_fresh(self, data: dict, now: datetime) -> set[str]:
        """Side-effects on a fresh Codex tick: history, resets, thresholds."""
        rl = data.get("rate_limit") or {}
        primary = rl.get("primary_window") or {}
        secondary = rl.get("secondary_window") or {}
        p_util = float(primary.get("used_percent") or 0)
        x_util = float(secondary.get("used_percent") or 0)
        p_reset = _codex_reset_dt(primary)
        x_reset = _codex_reset_dt(secondary)
        p_win = codex_window_label(primary.get("limit_window_seconds"))
        x_win = codex_window_label(secondary.get("limit_window_seconds"))
        p_label = t("codex_metric_label", win=p_win)
        x_label = t("codex_metric_label", win=x_win)

        reset_metrics = self._detect_resets_for(
            (
                ("codex_primary", p_util, p_label),
                ("codex_secondary", x_util, x_label),
            )
        )
        self._check_thresholds("codex_primary", p_label, p_util, p_reset)
        self._check_thresholds("codex_secondary", x_label, x_util, x_reset)

        self.history.append("codex_primary", p_util, now)
        self.history.append("codex_secondary", x_util, now)
        return reset_metrics

    def _detect_resets_for(
        self, triples: tuple[tuple[str, float, str], ...]
    ) -> set[str]:
        """Detect resets (>5pp drop) for the given (metric, util, label) tuples."""
        reset_metrics: set[str] = set()
        for metric, util, label in triples:
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
    # Honour the configured poll cadence (re-registrable live on a change).
    indicator.start_poll_timer()
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
