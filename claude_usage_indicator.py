#!/usr/bin/python3
"""Ubuntu AppIndicator for Claude Code usage (5h session + 7-day weekly).

Uses the system Python (`/usr/bin/python3`) explicitly because PyGObject
(`gi`) is provided by the Ubuntu `python3-gi` package and is typically
not available inside project virtualenvs.

Polls the undocumented OAuth endpoint `https://api.anthropic.com/api/oauth/usage`
— the same endpoint Claude Code's `/usage` command uses internally — and
surfaces the info in the GNOME top bar:

  * label:    "5h 17%  ·  7j 5%"
  * menu:     session + weekly details, reset times, countdowns, account,
              refresh, quit
  * notifs:   on reset (new window) and on crossing 80%/95% thresholds

Deps:
    sudo apt install gir1.2-ayatanaappindicator3-0.1 python3-gi \
                     python3-requests libnotify-bin

Run:
    /usr/bin/python3 claude_usage_indicator.py
"""

from __future__ import annotations

import json
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

import requests  # noqa: E402

CREDS_PATH = Path.home() / ".claude" / ".credentials.json"
CONFIG_PATH = Path.home() / ".claude.json"
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
POLL_SECONDS = 60
THRESHOLDS = (80, 95)
# Exponential backoff when the OAuth usage endpoint returns 429 (a known
# Anthropic-side bug — see claude-code issue #31021). Retries happen
# further and further apart, capped at the last value.
BACKOFF_STAGES = (120, 300, 900, 1800, 3600)
APP_ID = "claude-usage-indicator"
SETTINGS_URL = "https://claude.ai/settings/usage"

# Icons ship with the repo next to this script, so the repo is
# self-contained. If you prefer the XDG layout, drop PNGs with the same
# names into ~/.local/share/claude-usage-indicator/ and they take
# precedence.
_REPO_ICONS = Path(__file__).resolve().parent / "icons"
_XDG_ICONS = Path.home() / ".local/share/claude-usage-indicator"
FALLBACK_ICON = "dialog-information-symbolic"


def _icon_path(name: str) -> Path:
    # Resolve per-call so a user dropping files into the XDG dir is picked
    # up on the next tick without restarting the daemon.
    xdg = _XDG_ICONS / name
    return xdg if xdg.exists() else _REPO_ICONS / name


def pick_icon(f_util: float, s_util: float) -> str:
    """Return the icon path for the current usage state.

    * full-claude.png when the session or weekly limit is hit (>= 100%)
    * gray-claude.png when no session has started (5h window at 0%)
    * claude.png otherwise
    """
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
    """Try to open a terminal running ``claude`` to trigger OAuth login.

    Walks a list of common Linux terminal emulators and returns True if
    one was spawned successfully.
    """
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

_TIER_LABELS = {
    "default_claude_max_20x": "Max 20x",
    "default_claude_max_5x": "Max 5x",
    "default_claude_pro": "Pro",
    "default_claude_free": "Free",
}


def _safe_load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def read_token() -> str | None:
    data = _safe_load_json(CREDS_PATH)
    oauth = data.get("claudeAiOauth") or {}
    return oauth.get("accessToken") or data.get("accessToken")


def read_account() -> str:
    """Return a one-line account description: 'email · Plan'."""
    cfg = _safe_load_json(CONFIG_PATH)
    account = cfg.get("oauthAccount") or {}
    email = account.get("emailAddress") or account.get("displayName")

    creds = _safe_load_json(CREDS_PATH)
    oauth = creds.get("claudeAiOauth") or {}
    raw = oauth.get("rateLimitTier") or oauth.get("subscriptionType")
    tier = _TIER_LABELS.get(raw, raw) if raw else None

    parts = [p for p in (email, tier) if p]
    return "  ·  ".join(parts) if parts else "Compte inconnu"


def fetch_usage(token: str) -> dict:
    try:
        r = requests.get(
            USAGE_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "anthropic-beta": "oauth-2025-04-20",
                "Content-Type": "application/json",
            },
            timeout=10,
        )
    except requests.RequestException as e:
        # Avoid leaking auth-related context via str(e) — just the class.
        return {"error": f"request failed: {type(e).__name__}"}
    if r.status_code == 429:
        out: dict = {"error": "HTTP 429", "rate_limited": True}
        hint = r.headers.get("Retry-After")
        if hint:
            try:
                out["retry_after"] = max(0, int(hint))
            except ValueError:
                pass
        return out
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}"}
    try:
        return r.json()
    except ValueError:
        return {"error": "invalid JSON"}


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    # Treat naive strings as UTC — the OAuth endpoint is in UTC, and
    # downstream arithmetic compares against an aware "now".
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def format_remaining(reset: datetime | None) -> str:
    if not reset:
        return "–"
    delta = reset - datetime.now(timezone.utc)
    secs = int(delta.total_seconds())
    if secs <= 0:
        return "maintenant"
    hours, rem = divmod(secs, 3600)
    minutes = rem // 60
    if hours >= 24:
        days, hours = divmod(hours, 24)
        return f"{days}j {hours}h"
    if hours:
        return f"{hours}h{minutes:02d}"
    return f"{minutes}min"


def format_local(reset: datetime | None) -> str:
    if not reset:
        return "–"
    return reset.astimezone().strftime("%a %d %b %H:%M")


def progress_bar(util: float, width: int = 10) -> str:
    filled = max(0, min(width, int(round(util / 100 * width))))
    return "█" * filled + "░" * (width - filled)


def _fmt_secs(secs: int) -> str:
    if secs >= 3600:
        return f"{secs // 3600}h{(secs % 3600) // 60:02d}"
    if secs >= 60:
        return f"{secs // 60}min"
    return f"{secs}s"


class Indicator:
    def __init__(self) -> None:
        self.token = read_token()
        self.prev_f_util: float | None = None
        self.prev_s_util: float | None = None
        self.seen_5h: set[int] = set()
        self.seen_7d: set[int] = set()
        self.last_good: dict | None = None
        self.backoff_idx: int = 0
        self.backoff_until: datetime | None = None
        self.current_icon: str | None = None

        self.ind = AppIndicator.Indicator.new(
            APP_ID,
            pick_icon(0, 0),
            AppIndicator.IndicatorCategory.APPLICATION_STATUS,
        )
        self.ind.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.ind.set_label(" …", " 5h 100%  ·  7j 100% ")

        self._build_menu()
        self._set_connected(self.token is not None)
        self.ind.set_menu(self.menu)
        Notify.init(APP_ID)

    def _build_menu(self) -> None:
        self.menu = Gtk.Menu()

        self.item_account = Gtk.MenuItem(label=read_account())
        self.item_account.set_tooltip_text(f"Ouvrir {SETTINGS_URL}")
        self.item_account.connect(
            "activate", lambda _i: webbrowser.open(SETTINGS_URL)
        )

        self.item_login = Gtk.MenuItem(label="Se connecter à Claude Code")
        self.item_login.connect("activate", self._on_login_clicked)

        self.item_session = self._info_item("Session 5h  …")
        self.item_session_reset = self._info_item("    ↳ …")
        self.item_weekly = self._info_item("Weekly 7j   …")
        self.item_weekly_reset = self._info_item("    ↳ …")
        self.item_sonnet = self._info_item("Sonnet 7j   –")
        self.item_extra = self._info_item("Extra       désactivé")

        self.item_refresh = Gtk.MenuItem(label="Rafraîchir  (jamais)")
        self.item_refresh.connect("activate", self._on_refresh_clicked)
        quit_item = Gtk.MenuItem(label="Quitter")
        quit_item.connect("activate", lambda _i: Gtk.main_quit())

        sep_account = Gtk.SeparatorMenuItem()
        sep_session = Gtk.SeparatorMenuItem()
        sep_weekly = Gtk.SeparatorMenuItem()
        sep_refresh = Gtk.SeparatorMenuItem()
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
            sep_refresh,
            self.item_refresh,
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
        self.menu.show_all()

    @staticmethod
    def _info_item(label: str) -> Gtk.MenuItem:
        item = Gtk.MenuItem(label=label)
        item.set_sensitive(False)
        return item

    def notify(self, summary: str, body: str, urgent: bool = False) -> None:
        n = Notify.Notification.new(summary, body, "dialog-information")
        n.set_urgency(Notify.Urgency.CRITICAL if urgent else Notify.Urgency.NORMAL)
        try:
            n.show()
        except GLib.Error as e:
            # libnotify bus not available (headless, locked screen, broken
            # D-Bus) — log but keep running. Anything else propagates.
            print(f"notify failed: {e}", file=sys.stderr)

    def _set_connected(self, connected: bool) -> None:
        """Toggle the menu between connected and disconnected layouts."""
        for item in self._connected_only:
            if connected:
                item.show()
            else:
                item.hide()
        for item in self._disconnected_only:
            if connected:
                item.hide()
            else:
                item.show()
        self.item_account.set_label(
            read_account() if connected else "non connecté"
        )

    def _on_login_clicked(self, _item: Gtk.MenuItem) -> None:
        if spawn_claude_login():
            self.notify(
                "Claude Code",
                "Termine le login OAuth dans le terminal qui vient de s'ouvrir.",
            )
        else:
            self.notify(
                "Se connecter à Claude Code",
                "Aucun terminal trouvé. Ouvre-en un et tape `claude`.",
            )

    def _show_rate_limited(self, wait_secs: int) -> None:
        """Keep the previous stats on screen, switch icon to gray.

        The OAuth usage endpoint returns persistent 429s (issue #31021)
        that have nothing to do with the user's actual quota, so we
        preserve the last known values and just signal "stale" via the
        gray Claude icon and the refresh item label.
        """
        if self.last_good is not None:
            self._render(self.last_good, fresh=False)
        icon = _gray_icon()
        if icon != self.current_icon:
            self.ind.set_icon_full(icon, "Claude usage — rate-limited")
            self.current_icon = icon
        self.item_refresh.set_label(
            f"Rate-limited  (retry dans {_fmt_secs(wait_secs)})"
        )

    def _on_refresh_clicked(self, _item: Gtk.MenuItem) -> None:
        """Schedule a refresh after the menu has fully dismissed.

        Calling ``tick()`` synchronously inside the ``activate`` handler
        runs while the menu is still closing, which prevents AppIndicator
        from redrawing the top-bar label. ``GLib.idle_add`` defers the
        work to the next main-loop iteration.
        """
        self.item_refresh.set_label("Rafraîchir  (…)")
        GLib.idle_add(self._deferred_tick)

    def _deferred_tick(self) -> bool:
        self.tick()
        return False  # one-shot idle source

    def tick(self) -> bool:
        # Always re-check on every tick: the user may log in/out while
        # the daemon is running.
        self.token = read_token()
        if not self.token:
            self._set_connected(False)
            self.ind.set_label(" non connecté ", " non connecté ")
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
            default_wait = BACKOFF_STAGES[min(self.backoff_idx, len(BACKOFF_STAGES) - 1)]
            wait = int(data.get("retry_after") or default_wait)
            self.backoff_until = now + timedelta(seconds=wait)
            self.backoff_idx = min(self.backoff_idx + 1, len(BACKOFF_STAGES) - 1)
            self._show_rate_limited(wait)
            return True
        if "error" in data:
            self.ind.set_label(" !err ", "")
            self.item_session.set_label(f"Erreur : {data['error']}")
            self.item_refresh.set_label(
                f"Rafraîchir  (échec {now.astimezone().strftime('%H:%M:%S')})"
            )
            return True

        # Success: reset backoff state
        self.backoff_idx = 0
        self.backoff_until = None
        self.last_good = data
        self._render(data)
        return True

    def _render(self, data: dict, fresh: bool = True) -> None:
        """Render usage stats to the menu and top bar.

        When ``fresh`` is False the data is from ``self.last_good`` — we
        redraw the stats (so countdowns stay live) but skip state updates
        and icon changes. The caller is responsible for the icon in that
        case (e.g. gray during rate-limit).
        """
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
            self._detect_reset(
                "Session 5h", f_util, self.prev_f_util, self.seen_5h
            )
            self._detect_reset(
                "Weekly 7j", s_util, self.prev_s_util, self.seen_7d
            )
            self.prev_f_util = f_util
            self.prev_s_util = s_util

            self._check_thresholds(
                "Session 5h", f_util, f_reset, self.seen_5h
            )
            self._check_thresholds("Weekly 7j", s_util, s_reset, self.seen_7d)

        self.ind.set_label(
            f" 5h {f_util:.0f}%  ·  7j {s_util:.0f}% ",
            " 5h 100%  ·  7j 100% ",
        )

        if fresh:
            icon = pick_icon(f_util, s_util)
            if icon != self.current_icon:
                self.ind.set_icon_full(icon, "Claude usage")
                self.current_icon = icon

        self.item_session.set_label(
            f"Session 5h  [{progress_bar(f_util)}] {f_util:5.1f}%"
        )
        self.item_session_reset.set_label(
            f"    ↳ reset dans {format_remaining(f_reset)}  ({format_local(f_reset)})"
        )
        self.item_weekly.set_label(
            f"Weekly 7j   [{progress_bar(s_util)}] {s_util:5.1f}%"
        )
        self.item_weekly_reset.set_label(
            f"    ↳ reset dans {format_remaining(s_reset)}  ({format_local(s_reset)})"
        )
        if sonnet.get("resets_at") or so_util:
            self.item_sonnet.set_label(
                f"Sonnet 7j   [{progress_bar(so_util)}] {so_util:5.1f}%"
            )
        else:
            self.item_sonnet.set_label("Sonnet 7j   –")

        if extra.get("is_enabled"):
            used = extra.get("used_credits") or 0
            limit = extra.get("monthly_limit") or 0
            currency = extra.get("currency") or ""
            self.item_extra.set_label(f"Extra : {used}/{limit} {currency}".strip())
        else:
            self.item_extra.set_label("Extra : désactivé")

        self.item_refresh.set_label(
            f"Rafraîchir  ({datetime.now().strftime('%H:%M:%S')})"
        )

    def _detect_reset(
        self, label: str, util: float, prev: float | None, seen: set[int]
    ) -> None:
        """A reset fires when utilization drops by >5 percentage points.

        This is robust against server-side jitter on ``resets_at`` (the
        microseconds drift between polls, which used to trigger a false
        notif every tick).
        """
        if prev is not None and util + 5 < prev:
            self.notify(f"{label} réinitialisée", "Compteur revenu à zéro.")
            seen.clear()

    def _check_thresholds(
        self, label: str, util: float, reset: datetime | None, seen: set[int]
    ) -> None:
        for t in THRESHOLDS:
            if util >= t and t not in seen:
                seen.add(t)
                self.notify(
                    f"{label} à {util:.0f}%",
                    f"Reset {format_local(reset)} (dans {format_remaining(reset)})",
                    urgent=t >= 95,
                )


def main() -> int:
    ind = Indicator()
    ind.tick()
    GLib.timeout_add_seconds(POLL_SECONDS, ind.tick)
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
