#!/usr/bin/env python3
"""Windows system-tray version of Claude Usage Tab.

Third front-end onto the same engine as the Linux indicator
(``claude_usage_indicator.py``) and the macOS menu bar
(``claude_usage_menubar.py``): ``api``, ``settings``, ``alerts``,
``accounts``, ``updates``, ``costs``, ``topbar``, ``strings``, ``formatting``
and ``sound`` are shared verbatim. Only the presentation layer is new.

The notification area is stingier than the other two shells — it gives you an
icon and a 127-character tooltip, and that is all. So the split is:

* **icon** — the utilisation percentage, drawn into a coloured tile by
  ``trayicon``. It is the only thing visible without hovering, so it carries
  the single number that matters (the highest selected metric).
* **tooltip** — the composed top-bar label plus as many detail lines as fit.
* **menu** — everything else, same rows as the other two front-ends.

Threading, which is the part worth getting right:

``pystray`` owns the main thread for the Win32 message loop, and hands us a
worker thread via ``Icon.run(setup=...)``. That worker is where *everything*
happens — polling, rendering, settings writes, modal dialogs. Menu callbacks
fire on the message-loop thread, so they only ever drop a job on a queue: a
callback that blocked (a confirmation dialog, an HTTP round-trip) would
freeze the tray icon itself. ``costs`` gets its own short-lived thread on top
of that, because a ccusage scan takes seconds and must not stall polling.

Requires ``pystray``, ``Pillow`` and ``requests``
(``pip install -r requirements-windows.txt``).
"""

from __future__ import annotations

import dataclasses
import os
import queue
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pystray
from pystray import Menu, MenuItem

import accounts
import attention
import attention_hooks
import costs
import handoff
import sound
import trayicon
import updates
import winshell
from alerts import History, evaluate_alerts
from api import fetch_usage, parse_iso, read_account, read_token
from formatting import (
    fmt_secs,
    format_age,
    format_cost,
    format_local,
    format_remaining,
    format_stamp,
    progress_bar,
    to_float,
)
from settings import (
    APP_ID,
    SETTINGS_PATH,
    VALID_CLAUDE_TOPBAR_METRICS,
    VALID_SOUND_MODES,
    Settings,
    load_settings,
    mtime as settings_mtime,
    save_settings,
)
from strings import (
    SUPPORTED_LANGS,
    current_lang,
    detect_lang,
    set_lang,
    setup_locale,
    t,
)
from topbar import compose_label

SETTINGS_URL = "https://claude.ai/settings/usage"
INSTALL_DIR = Path(__file__).resolve().parent
ASSETS_DIR = INSTALL_DIR / "assets"
SCRIPT_PATH = Path(__file__).resolve()
UPDATE_SCRIPT = "update-windows.ps1"

BACKOFF_STAGES = (120, 300, 900, 1800, 3600)
BOOT_COOLDOWN = timedelta(minutes=5)
HISTORY_SNAPSHOT_EVERY = 5
UPDATE_CHECK_DELAY = 5  # seconds after launch before the first update check
MIN_POLL_SECONDS = 10

# Terminal-attention blink cadences. Nothing here rides the usage poll: a
# hook fires the moment a turn ends, and waiting two minutes to notice would
# defeat the point. Scanning the flag directory is one ``iterdir`` over a
# handful of tiny files, so a one-second beat costs nothing measurable.
ATTENTION_RESCAN_SECONDS = 1.0
ATTENTION_IDLE_SECONDS = 1.0
ATTENTION_OFF_SECONDS = 5.0

APP_TITLE = "Claude Usage Tab"

# An unset LOCALAPPDATA is dropped rather than joined onto: Path("") / x is a
# *relative* path, which would quietly make both the log and icon lookup
# depend on the working directory.
_LOCAL_APPDATA = os.environ.get("LOCALAPPDATA", "").strip()
_APPDATA_DIR = (Path(_LOCAL_APPDATA) if _LOCAL_APPDATA else Path.home()) / APP_ID

# Where stderr goes when there is nowhere for it to go. Rotated by size, not
# by date — one file, bounded, that a bug report can be pasted from.
LOG_PATH = _APPDATA_DIR / "tray.log"
LOG_MAX_BYTES = 1_000_000

# Icon overrides, most specific first. %LOCALAPPDATA% is where a Windows user
# would think to drop them; the XDG path is kept so a dotfiles repo shared
# with a Linux box keeps working.
_ICON_DIRS = tuple(
    d
    for d in (
        _APPDATA_DIR if _LOCAL_APPDATA else None,
        Path.home() / ".local" / "share" / APP_ID,
        INSTALL_DIR / "icons",
    )
    if d is not None
)


def _icon_file(name: str) -> Path | None:
    """First existing copy of an icon file, or ``None``."""
    for directory in _ICON_DIRS:
        candidate = directory / name
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def _direct(fn, *args):
    """Adapt a call to pystray's ``(icon, item)`` action signature.

    ``MenuItem`` inspects ``__code__.co_argcount`` and rejects anything that
    wants more than two arguments — which rules out the usual
    ``lambda i, it, x=value: ...`` capture trick, since default arguments
    count. Wrapping the value in a closure here is the way to bind loop
    variables safely.

    The wrapped call runs **on the message-loop thread**, so reserve this for
    work that returns immediately. Everything else goes through
    :func:`_queued`.
    """

    def run(_icon, _item):
        return fn(*args)

    return run


def _flag(value: bool):
    """A ``checked=`` callable. pystray rejects a bare bool (it must be callable)."""
    return lambda _item: value


class _Icon(pystray.Icon):
    """A tray icon whose native-menu rebuilds are serialised.

    pystray rebuilds the menu on the message-loop thread after *every* click,
    and we rebuild it again from the worker after every poll. Both land in
    ``Icon._update_menu``, which destroys the current ``HMENU`` and creates a
    new one with no lock of its own — interleave the two and one thread frees
    the handle the other just published, leaving the tray with a menu that
    silently fails to open. The window is narrow and the mutex is free, so we
    close it rather than hope.
    """

    _menu_lock = threading.Lock()

    def _update_menu(self) -> None:
        with self._menu_lock:
            super()._update_menu()


class TrayApp:
    def __init__(self) -> None:
        settings = load_settings()
        lang = detect_lang(settings.lang)
        set_lang(lang)
        setup_locale(lang)

        self.settings: Settings = settings
        self.settings_mtime: float = settings_mtime()
        self.token: str | None = None
        self.prev_util: dict[str, float] = {}
        self.seen_thresholds: dict[str, set[int]] = {
            "five_hour": set(),
            "seven_day": set(),
        }
        self.last_good: dict | None = None
        self.backoff_idx: int = 0
        self.backoff_until: datetime | None = None

        self.history = History()
        self.history.load_from_disk()
        self.tick_counter: int = 0
        self.alert_last_fired: dict[str, datetime] = {}
        self.active_alerts: dict[str, str] = {}
        self.boot_cooldown_until: datetime = datetime.now(timezone.utc) + BOOT_COOLDOWN
        self.update_info: updates.UpdateInfo | None = updates.cached()
        self.cost_report: costs.CostReport | None = (
            costs.cached() if self.settings.cost.enabled else None
        )
        self._cost_thread: threading.Thread | None = None
        self.account_states: list[dict] = []
        self._last_claude_state: dict | None = None

        # Terminal-attention blink. ``_base_icon`` is the resting frame the
        # last poll composed, kept so a blink can restore it without redoing
        # the whole render; ``_blink_on`` is which half of the cycle we're in.
        self._attention = attention.Snapshot()
        self._base_icon = None
        self._badge_text: str | None = None
        self._blink_on = False
        self._att_next_scan = 0.0
        self._att_signature: tuple[int, int] = (0, 0)
        self._hooks_installed: bool | None = None

        # Standing offer to hand the work to another account, recomputed on
        # every poll; the flag keeps the toast to one per limit episode.
        self._offer: handoff.Offer | None = None
        self._offer_notified = False

        # Menu clicks land on the message-loop thread and must return at once;
        # they push work here instead. A job returning True asks for an
        # immediate poll rather than waiting out the rest of the interval.
        self._jobs: queue.Queue = queue.Queue()
        self._running = True

        self.icon = _Icon(
            "claude-usage-tab",
            icon=trayicon.fallback(),
            title=APP_TITLE,
            menu=Menu(self._menu_items),
        )

    # ------------------------------------------------------------------ loop

    def run(self) -> None:
        """Blocking. Owns the main thread for the Win32 message loop."""
        self.icon.run(setup=self._worker)

    def _worker(self, icon: pystray.Icon) -> None:
        """The setup thread: polling, rendering and every queued job."""
        icon.visible = True
        next_poll = 0.0
        next_update = time.monotonic() + UPDATE_CHECK_DELAY
        next_blink = 0.0
        first_update = True

        while self._running:
            now = time.monotonic()
            if now >= next_poll:
                self._safe(self._run_tick, "tick")
                next_poll = time.monotonic() + max(
                    MIN_POLL_SECONDS, self.settings.poll_seconds
                )
            if time.monotonic() >= next_update:
                # Only the startup check bypasses the throttle, exactly as on
                # the other two front-ends.
                forced, first_update = first_update, False
                self._safe(lambda: self._check_update(force=forced), "update check")
                next_update = time.monotonic() + updates.PERIODIC_INTERVAL
            if time.monotonic() >= next_blink:
                self._safe(self._blink_step, "blink")
                next_blink = time.monotonic() + self._blink_interval()

            deadlines = [next_poll, next_update, next_blink]
            timeout = max(0.05, min(deadlines) - time.monotonic())
            try:
                job = self._jobs.get(timeout=timeout)
            except queue.Empty:
                continue
            if self._safe(job, "menu action") is True:
                next_poll = 0.0

    @staticmethod
    def _safe(fn, what: str):
        """Run ``fn``, logging anything it raises. A bad tick must not end the loop."""
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            print(f"{what} error: {type(e).__name__}: {e}", file=sys.stderr)
            return None

    def _enqueue(self, fn, *args) -> None:
        self._jobs.put(lambda: fn(*args))

    def _queued(self, fn, *args):
        """A menu action that hands the work to the worker thread.

        The default for every callback: the message-loop thread must return
        immediately or the tray icon stops responding to clicks — and a
        modal confirmation or an HTTP round-trip would hold it for seconds.
        """

        def run(_icon, _item):
            self._enqueue(fn, *args)

        return run

    def _quit(self) -> None:
        """Stop the worker first, then the message loop.

        ``Icon.stop()`` joins the setup thread, so clearing the flag and
        nudging the queue first is what keeps quitting instant instead of
        waiting out the poll interval.
        """
        self._running = False
        self._jobs.put(lambda: None)
        self.icon.stop()

    # ------------------------------------------------------------------ tick

    def _run_tick(self) -> None:
        self._reload_settings_if_needed()
        self.token = read_token()
        now = datetime.now(timezone.utc)
        claude_state = self._tick_claude(now) if self.token else None

        self.account_states = []
        if self.settings.accounts_enabled:
            if self.token:
                accounts.capture(now)
            self.account_states = self._tick_accounts(now, claude_state)

        self._render(claude_state, now)
        self._refresh_costs()
        self._safe(self._refresh_hook_state, "hook state")

    def _reload_settings_if_needed(self) -> None:
        current = settings_mtime()
        if not current or current == self.settings_mtime:
            return
        self.settings = load_settings()
        self.settings_mtime = current
        new_lang = detect_lang(self.settings.lang)
        if new_lang != current_lang():
            set_lang(new_lang)
            setup_locale(new_lang)

    def _tick_claude(self, now: datetime) -> dict:
        if self.backoff_until and now < self.backoff_until:
            wait = int((self.backoff_until - now).total_seconds())
            return self._state("rate_limited", self.last_good, False, max(0, wait))

        data = fetch_usage(self.token)

        if data.get("rate_limited"):
            default_wait = BACKOFF_STAGES[min(self.backoff_idx, len(BACKOFF_STAGES) - 1)]
            wait = int(data.get("retry_after") or default_wait)
            self.backoff_until = now + timedelta(seconds=wait)
            self.backoff_idx = min(self.backoff_idx + 1, len(BACKOFF_STAGES) - 1)
            return self._state("rate_limited", self.last_good, False, wait)

        if "error" in data:
            return self._state("error", self.last_good, False, None, data["error"])

        self.backoff_idx = 0
        self.backoff_until = None
        self.last_good = data
        return self._state("ok", data, True, None)

    @staticmethod
    def _state(
        status: str,
        data: dict | None,
        fresh: bool,
        wait_secs: int | None,
        error_msg: str | None = None,
    ) -> dict:
        return {
            "status": status,
            "data": data,
            "fresh": fresh,
            "wait_secs": wait_secs,
            "error_msg": error_msg,
        }

    # -------------------------------------------------------------- accounts

    def _tick_accounts(self, now: datetime, claude_state: dict | None) -> list[dict]:
        active = accounts.active_id()
        states: list[dict] = []
        for acct in accounts.list_accounts():
            if acct.id == active:
                states.append(
                    {
                        "acct": acct,
                        "active": True,
                        "status": claude_state.get("status") if claude_state else "error",
                        "data": claude_state.get("data") if claude_state else None,
                    }
                )
            else:
                states.append(self._poll_stored_account(acct, now))
        return states

    def _poll_stored_account(self, acct: accounts.Account, now: datetime) -> dict:
        blob = accounts.load_blob(acct.id) or {}
        oauth = blob.get("claudeAiOauth") or {}
        token = oauth.get("accessToken")
        if not token:
            return {"acct": acct, "active": False, "status": "expired", "data": None}
        if accounts.token_expired(oauth, now):
            token = accounts.refresh(acct.id)
            if not token:
                return {"acct": acct, "active": False, "status": "expired", "data": None}
        data = fetch_usage(token)
        if data.get("error") == "HTTP 401":
            token = accounts.refresh(acct.id)
            if token:
                data = fetch_usage(token)
        if data.get("rate_limited"):
            return {"acct": acct, "active": False, "status": "rate_limited", "data": None}
        if "error" in data:
            status = "expired" if data.get("error") == "HTTP 401" else "error"
            return {"acct": acct, "active": False, "status": status, "data": None}
        return {"acct": acct, "active": False, "status": "ok", "data": data}

    def _account_usage_text(self, st: dict) -> str:
        status = st.get("status")
        if status == "ok" and st.get("data"):
            data = st["data"]
            five = to_float((data.get("five_hour") or {}).get("utilization"))
            seven = to_float((data.get("seven_day") or {}).get("utilization"))
            return t("acct_usage", five=five, seven=seven)
        if status == "rate_limited":
            return t("acct_usage_rl")
        if status == "expired":
            return t("acct_usage_expired")
        return t("acct_usage_error")

    # ---------------------------------------------------------------- render

    def _render(self, claude_state: dict | None, now: datetime) -> None:
        self._last_claude_state = claude_state

        reset_metrics: set[str] = set()
        if claude_state and claude_state["status"] == "ok" and claude_state["fresh"]:
            reset_metrics = self._on_fresh_tick(claude_state["data"], now)

        if claude_state and claude_state["fresh"]:
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
                )
                self.active_alerts[f.alert_id] = f.label
            if reset_metrics:
                stale = [
                    a.id
                    for a in self.settings.alerts
                    if a.id in self.active_alerts and a.metric in reset_metrics
                ]
                for aid in stale:
                    self.active_alerts.pop(aid, None)
            self.tick_counter += 1
            if self.tick_counter % HISTORY_SNAPSHOT_EVERY == 0:
                self.history.snapshot_to_disk()

        self._refresh_offer(claude_state)
        self._apply_visuals(claude_state)

    # -- limit-reached handoff offer ---------------------------------------

    def _refresh_offer(self, claude_state: dict | None) -> None:
        """Work out whether to offer a switch, and say so once per episode.

        Only meaningful with the switcher enabled: proposing a move the user
        has not allowed would be an advert for a disabled feature.
        """
        if not (self.settings.accounts_enabled and self.settings.account_switch_enabled):
            self._offer = None
            self._offer_notified = False
            return

        data = (claude_state or {}).get("data") or {}
        active_util = to_float((data.get("five_hour") or {}).get("utilization"))
        candidates = [
            (
                st["acct"].id,
                st["acct"].email or st["acct"].label,
                to_float(((st.get("data") or {}).get("five_hour") or {}).get("utilization")),
            )
            for st in self.account_states
            if not st.get("active") and st.get("status") == "ok" and st.get("data")
        ]
        projects = handoff.recent_projects(1)
        self._offer = handoff.pick_offer(
            active_util, candidates, projects[0] if projects else None
        )

        if self._offer is None:
            # Cleared on the way back down, so the next limit notifies again.
            self._offer_notified = False
            return
        if not self._offer_notified:
            self._offer_notified = True
            self.notify(
                t("ho_offer_title"),
                t(
                    "ho_offer_body",
                    email=self._offer.email,
                    util=int(self._offer.util),
                    project=self._offer.project.name,
                ),
            )

    def _apply_visuals(self, claude_state: dict | None) -> None:
        """Push icon, tooltip and menu to the shell. Worker thread only."""
        self._base_icon = self._compose_icon(claude_state)
        # A poll always lands on the resting frame; the blink timer takes the
        # icon back over on its next beat.
        self._blink_on = False
        self.icon.icon = self._base_icon
        self.icon.title = winshell.clamp(
            self._compose_tooltip(claude_state), winshell.MAX_TIP
        )
        self.icon.update_menu()

    # -- attention blink ---------------------------------------------------

    def _blink_interval(self) -> float:
        """Seconds until the next blink beat, given what is flagged."""
        att = self.settings.attention
        if not att.enabled:
            return ATTENTION_OFF_SECONDS
        kind = self._attention.kind
        if kind is None:
            return ATTENTION_IDLE_SECONDS
        ms = att.waiting_ms if kind == attention.KIND_WAITING else att.blink_ms
        return ms / 1000.0

    def _blink_step(self) -> None:
        """One beat: rescan if due, then flip the icon. Worker thread only."""
        att = self.settings.attention
        if not att.enabled:
            self._rest()
            return

        now = time.monotonic()
        if now >= self._att_next_scan:
            self._attention = attention.scan(att.expire_minutes)
            self._att_next_scan = now + ATTENTION_RESCAN_SECONDS
            signature = (len(self._attention.waiting), len(self._attention.done))
            if signature != self._att_signature:
                # The menu carries the count, so it follows the scan — but
                # only when the count actually moved. Rebuilding the native
                # menu every second for nothing is exactly the churn the
                # ``_Icon`` mutex exists to survive.
                self._att_signature = signature
                self.icon.update_menu()

        kind = self._attention.kind
        if kind is None:
            self._rest()
            return

        self._blink_on = not self._blink_on
        self.icon.icon = (
            self._attention_frame(kind) if self._blink_on else self._resting_icon()
        )

    def _rest(self) -> None:
        """Put the resting frame back, if a blink left the other one up."""
        if not self._blink_on:
            return
        self._blink_on = False
        self.icon.icon = self._resting_icon()

    def _resting_icon(self):
        return self._base_icon if self._base_icon is not None else trayicon.fallback()

    def _attention_frame(self, kind: str):
        """The lit half of the cycle: same glyph if we have one, else a tile."""
        if self._badge_text:
            return trayicon.attention_badge(self._badge_text, kind)
        return trayicon.attention_dot(kind)

    # -- icon -------------------------------------------------------------

    def _metric_values(self, data: dict) -> list[float]:
        """Utilisation of each metric the user put in the label, in order."""
        paths = {
            "five_hour": "five_hour",
            "seven_day": "seven_day",
            "seven_day_sonnet": "seven_day_sonnet",
        }
        out: list[float] = []
        for metric in self.settings.topbar.claude_metrics:
            node = data.get(paths.get(metric, "")) or {}
            out.append(to_float(node.get("utilization")))
        return out

    def _compose_icon(self, claude_state: dict | None):
        """The tray image: a number badge when we have one, else a logo.

        Only one number fits in a 16-pixel square, so it is the **highest** of
        the selected metrics — the one nearest a limit, which is the one worth
        interrupting for. The rest live in the tooltip.
        """
        tb = self.settings.topbar
        status = (claude_state or {}).get("status")
        data = (claude_state or {}).get("data") or {}
        fresh = bool((claude_state or {}).get("fresh"))
        # Remembered for the blink, which reuses the glyph so the number stays
        # readable through the cycle. ``None`` means we're in logo mode.
        self._badge_text = None

        if claude_state is None:
            return self._logo_icon("gray-claude.png")

        values = self._metric_values(data)
        if not tb.show_claude or not values:
            # The user asked for icon-only; mirror the GTK icon states.
            f_util = to_float((data.get("five_hour") or {}).get("utilization"))
            s_util = to_float((data.get("seven_day") or {}).get("utilization"))
            if status != "ok":
                return self._logo_icon("gray-claude.png")
            if f_util >= 100 or s_util >= 100:
                return self._logo_icon("full-claude.png")
            if f_util == 0:
                return self._logo_icon("gray-claude.png")
            return self._logo_icon("claude.png")

        peak = max(values)
        self._badge_text = trayicon.badge_text(peak)
        return trayicon.badge(self._badge_text, trayicon.state_color(peak, fresh))

    def _logo_icon(self, name: str):
        path = _icon_file(name)
        image = trayicon.logo(path) if path else None
        return image if image is not None else trayicon.fallback()

    # -- tooltip ----------------------------------------------------------

    def _compose_tooltip(self, claude_state: dict | None) -> str:
        """Header plus as many detail lines as the 127-character cap allows.

        Lines are added while they fit rather than truncated mid-word, so a
        long account name costs you the *last* line instead of mangling it.
        """
        body, _guide = compose_label(
            claude_state, self.settings.topbar, bool(self.active_alerts)
        )
        header = APP_TITLE
        if body is None:
            header = f"{APP_TITLE} — {t('label_not_connected').strip()}"
        elif body.strip():
            header = f"{APP_TITLE} — {body.strip()}"

        candidates = list(self._metric_lines((claude_state or {}).get("data") or {}))
        attention_line = self._attention_line()
        if attention_line:
            candidates.insert(0, attention_line)
        if claude_state and claude_state.get("status") == "rate_limited":
            wait = claude_state.get("wait_secs")
            if wait:
                candidates.insert(0, t("rate_limited", d=fmt_secs(int(wait))))

        tip = winshell.clamp(header, winshell.MAX_TIP)
        for line in candidates:
            nxt = f"{tip}\n{line}"
            if len(nxt) > winshell.MAX_TIP:
                break
            tip = nxt
        return tip

    def _metric_lines(self, data: dict) -> list[str]:
        if not data:
            return []
        five = data.get("five_hour") or {}
        seven = data.get("seven_day") or {}
        sonnet = data.get("seven_day_sonnet") or {}
        extra = data.get("extra_usage") or {}
        f_util = to_float(five.get("utilization"))
        s_util = to_float(seven.get("utilization"))
        so_util = to_float(sonnet.get("utilization"))
        f_reset = parse_iso(five.get("resets_at"))
        s_reset = parse_iso(seven.get("resets_at"))

        lines = [
            t(
                "session_line",
                bar=progress_bar(f_util),
                util=f_util,
                rem=format_remaining(f_reset),
            ),
            t(
                "weekly_line",
                bar=progress_bar(s_util),
                util=s_util,
                rem=format_remaining(s_reset),
            ),
        ]
        if sonnet.get("resets_at") or so_util:
            lines.append(t("sonnet_line", bar=progress_bar(so_util), util=so_util))
        if extra.get("is_enabled"):
            lines.append(
                t(
                    "extra_active",
                    used=extra.get("used_credits") or 0,
                    limit=extra.get("monthly_limit") or 0,
                    currency=extra.get("currency") or "",
                ).strip()
            )
        return lines

    # -- thresholds / resets ----------------------------------------------

    def _on_fresh_tick(self, data: dict, now: datetime) -> set[str]:
        five = data.get("five_hour") or {}
        seven = data.get("seven_day") or {}
        sonnet = data.get("seven_day_sonnet") or {}
        f_util = to_float(five.get("utilization"))
        s_util = to_float(seven.get("utilization"))
        so_util = to_float(sonnet.get("utilization"))
        f_reset = parse_iso(five.get("resets_at"))
        s_reset = parse_iso(seven.get("resets_at"))

        reset_metrics = self._detect_resets(
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

    def _detect_resets(self, triples: tuple[tuple[str, float, str], ...]) -> set[str]:
        reset_metrics: set[str] = set()
        for metric, util, label in triples:
            prev = self.prev_util.get(metric)
            if prev is not None and util + 5 < prev:
                self.notify(t("reset_title", label=label), t("reset_body"))
                self.seen_thresholds.get(metric, set()).clear()
                reset_metrics.add(metric)
            self.prev_util[metric] = util
        return reset_metrics

    def _check_thresholds(
        self, metric: str, label: str, util: float, reset: datetime | None
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
                )
                if metric == "five_hour" and threshold == 80:
                    sound.play_for(self.settings.sound, ASSETS_DIR)

    def notify(self, title: str, body: str) -> None:
        """Balloon/toast via the tray icon. Both fields have hard length caps."""
        try:
            self.icon.notify(
                winshell.clamp(body, winshell.MAX_INFO),
                winshell.clamp(title, winshell.MAX_INFO_TITLE),
            )
        except Exception as e:  # noqa: BLE001 — a failed toast is not fatal
            print(f"notify failed: {type(e).__name__}: {e}", file=sys.stderr)

    # ------------------------------------------------------------------ menu

    def _menu_items(self):
        """Rebuilt on every ``update_menu()`` — Windows bakes the menu ahead."""
        claude_state = self._last_claude_state
        data = (claude_state or {}).get("data") or {}

        yield MenuItem(read_account(), _direct(winshell.open_path, SETTINGS_URL))
        if not self.token:
            yield MenuItem(t("login_item"), self._queued(self._on_login))
        for line in self._metric_lines(data):
            yield MenuItem(line, None, enabled=False)

        if self._offer is not None:
            # Top of the menu on purpose: this row exists because the user is
            # blocked right now, and it is the only thing they came here for.
            yield Menu.SEPARATOR
            yield MenuItem(
                t(
                    "ho_offer_row",
                    email=self._offer.email,
                    project=self._offer.project.name,
                ),
                self._queued(
                    self._on_switch_resume,
                    self._offer.account_id,
                    self._offer.email,
                    self._offer.project.path,
                ),
            )

        if self.settings.attention.enabled and self._attention.total:
            yield Menu.SEPARATOR
            yield MenuItem(
                t("att_menu_title", n=self._attention.total),
                Menu(*self._attention_items()),
            )

        if self.settings.accounts_enabled and self.account_states:
            yield Menu.SEPARATOR
            yield MenuItem(t("accounts_menu"), Menu(*self._account_items()))

        if costs.is_available(self.settings.cost):
            yield Menu.SEPARATOR
            yield MenuItem(self._cost_title(), Menu(*self._cost_items()))

        if self.active_alerts:
            yield Menu.SEPARATOR
            yield MenuItem("  ·  ".join(self.active_alerts.values()), None, enabled=False)
            yield MenuItem(t("clear_alerts"), self._queued(self._on_clear_alerts))

        yield Menu.SEPARATOR
        if (
            self.update_info
            and self.update_info.available
            and self.settings.update_check_enabled
        ):
            yield MenuItem(
                t("update_available", ver=updates.display(self.update_info.latest)),
                self._queued(self._on_update),
            )
        # Default item: a left-click on the tray icon refreshes. It is the one
        # action that is always safe and always gives visible feedback.
        yield MenuItem(
            self._refresh_label(claude_state), self._queued(self._on_refresh), default=True
        )
        yield MenuItem(t("menu_options"), Menu(*self._options_items()))
        yield MenuItem(
            t("edit_settings"), _direct(winshell.open_path, str(SETTINGS_PATH))
        )
        yield Menu.SEPARATOR
        yield MenuItem(t("dlg_version", ver=updates.current_version()), None, enabled=False)
        yield MenuItem(t("quit"), _direct(self._quit))

    def _attention_items(self):
        """One row per flagged session, then the "stop blinking" escape."""
        for session in self._attention.sessions:
            state = t(
                "att_state_waiting"
                if session.kind == attention.KIND_WAITING
                else "att_state_done"
            )
            label = t(
                "att_session",
                project=session.project or session.session_id[:12],
                state=state,
            )
            yield MenuItem(label, None, enabled=False)
        yield Menu.SEPARATOR
        yield MenuItem(t("att_clear"), self._queued(self._on_clear_attention))

    def _attention_line(self) -> str:
        """Tooltip fragment for what is flagged, or "" when nothing is."""
        snap = self._attention
        if not self.settings.attention.enabled or not snap.total:
            return ""
        parts = []
        if snap.waiting:
            parts.append(t("att_tip_waiting", n=len(snap.waiting)))
        if snap.done:
            parts.append(t("att_tip_done", n=len(snap.done)))
        return "  ·  ".join(parts)

    def _on_clear_attention(self) -> None:
        attention.clear_all()
        self._attention = attention.Snapshot()
        self._att_signature = (0, 0)
        self._att_next_scan = time.monotonic() + ATTENTION_RESCAN_SECONDS
        self._rest()
        self.icon.update_menu()

    def _refresh_label(self, claude_state: dict | None) -> str:
        if claude_state and claude_state.get("fresh"):
            return t("refresh_ts", ts=datetime.now().strftime("%H:%M:%S"))
        if (
            claude_state
            and claude_state["status"] == "rate_limited"
            and claude_state["wait_secs"]
        ):
            return t("rate_limited", d=fmt_secs(claude_state["wait_secs"]))
        if claude_state and claude_state["status"] == "error":
            return t("refresh_fail", ts=datetime.now().strftime("%H:%M:%S"))
        return t("refresh_never")

    def _account_items(self):
        for st in self.account_states:
            acct: accounts.Account = st["acct"]
            active = bool(st.get("active"))
            mark = t("acct_active_mark") if active else t("acct_inactive_mark")
            email = acct.email or acct.label
            plan = f" ({acct.plan})" if acct.plan else ""
            rows = []
            if active:
                rows.append(MenuItem(t("acct_switch_active"), None, enabled=False))
            elif not self.settings.account_switch_enabled:
                rows.append(MenuItem(t("acct_switch_disabled"), None, enabled=False))
            else:
                rows.append(
                    MenuItem(t("acct_switch"), self._queued(self._on_switch, acct.id, email))
                )
                rows.append(
                    MenuItem(
                        t("ho_resume_menu"),
                        Menu(*self._resume_items(acct.id, email)),
                    )
                )
            if not active:
                rows.append(
                    MenuItem(t("acct_forget"), self._queued(self._on_forget, acct.id, email))
                )
            yield MenuItem(
                f"{mark} {email}{plan}  —  {self._account_usage_text(st)}",
                Menu(*rows),
            )

    def _resume_items(self, acct_id: str, email: str):
        """Recent project directories to reopen the conversation in.

        Built on the message-loop thread like the rest of the menu, so it
        stays a single JSON read plus a handful of ``is_dir`` calls. The
        process probe, which is expensive, waits until the click.
        """
        projects = handoff.recent_projects()
        if not projects:
            yield MenuItem(t("ho_no_projects"), None, enabled=False)
            return
        for project in projects:
            age = handoff.age_hours(project)
            label = (
                t("ho_project_row", name=project.name)
                if age is None
                else t(
                    "ho_project_age",
                    name=project.name,
                    age=format_age(age * 3600),
                )
            )
            yield MenuItem(
                label,
                self._queued(self._on_switch_resume, acct_id, email, project.path),
            )

    # -- API cost (ccusage) ------------------------------------------------

    def _cost_title(self) -> str:
        if self.cost_report is None:
            return t("cost_menu_pending")
        return t("cost_menu_title", amount=format_cost(self.cost_report.today))

    def _cost_items(self):
        report = self.cost_report
        if report is None:
            # A runner exists (we are past is_available) but nothing has been
            # computed yet — say so rather than showing zeros, which would
            # read as "you spent nothing today".
            dash = t("dash")
            rows = [
                t("cost_today", amount=dash),
                t("cost_7d", amount=dash),
                t("cost_month", amount=dash),
                t("cost_pending"),
            ]
        else:
            rows = [
                t("cost_today", amount=format_cost(report.today)),
                t("cost_7d", amount=format_cost(report.last_7d)),
                t("cost_month", amount=format_cost(report.month)),
                t("cost_stale")
                if report.stale
                else t("cost_checked", when=format_stamp(report.checked_at)),
            ]
        for line in rows:
            yield MenuItem(line, None, enabled=False)
        yield MenuItem(t("cost_refresh"), self._queued(self._on_cost_refresh))

    def _refresh_costs(self, force: bool = False) -> None:
        """Kick a background ccusage run; a no-op while one is in flight.

        Even the worker thread is too precious for this: a scan walks the
        whole transcript tree and would stall polling for seconds.
        """
        if not costs.is_available(self.settings.cost):
            return
        if self._cost_thread is not None and self._cost_thread.is_alive():
            return
        cost_settings = self.settings.cost

        def run() -> None:
            try:
                report = costs.check(cost_settings, force=force)
            except Exception as e:  # noqa: BLE001 — a worker must not kill us
                print(f"cost check error: {type(e).__name__}: {e}", file=sys.stderr)
                return
            # Hand the result back through the queue so the redraw happens on
            # the worker thread, like every other UI update.
            self._enqueue(self._apply_cost, report)

        self._cost_thread = threading.Thread(target=run, name="ccusage", daemon=True)
        self._cost_thread.start()

    def _apply_cost(self, report: costs.CostReport | None) -> None:
        self.cost_report = report
        self._apply_visuals(self._last_claude_state)

    def _on_cost_refresh(self) -> None:
        # Already on the worker thread (the menu item is queued), so this only
        # kicks the ccusage thread — it does not block here.
        self._refresh_costs(force=True)

    # -- options submenu ---------------------------------------------------

    def _options_items(self):
        tb = self.settings.topbar

        yield MenuItem(
            t("dlg_lang"),
            Menu(
                *[
                    MenuItem(
                        t(f"dlg_lang_{code}"),
                        self._queued(self._set_language, code),
                        checked=_flag(current_lang() == code),
                        radio=True,
                    )
                    for code in SUPPORTED_LANGS
                ]
            ),
        )
        yield MenuItem(
            t("dlg_show_claude"),
            self._queued(self._toggle_topbar, "show_claude"),
            checked=_flag(tb.show_claude),
        )
        yield MenuItem(
            t("dlg_compact"),
            self._queued(self._toggle_topbar, "compact"),
            checked=_flag(tb.compact),
        )
        yield MenuItem(
            t("dlg_metric_labels"),
            self._queued(self._toggle_topbar, "metric_labels"),
            checked=_flag(tb.metric_labels),
        )
        yield MenuItem(
            t("dlg_provider_prefix"),
            self._queued(self._toggle_topbar, "show_provider_prefix"),
            checked=_flag(tb.show_provider_prefix),
        )
        yield MenuItem(
            t("dlg_tab_topbar"),
            Menu(
                *[
                    MenuItem(
                        t(f"dlg_metric_{key}"),
                        self._queued(self._toggle_metric, key),
                        checked=_flag(key in tb.claude_metrics),
                    )
                    for key in VALID_CLAUDE_TOPBAR_METRICS
                ]
            ),
        )

        yield Menu.SEPARATOR
        yield MenuItem(
            t("dlg_accounts_enabled"),
            self._queued(self._toggle_setting, "accounts_enabled"),
            checked=_flag(self.settings.accounts_enabled),
        )
        yield MenuItem(
            t("dlg_account_switch_enabled"),
            self._queued(self._toggle_setting, "account_switch_enabled"),
            checked=_flag(self.settings.account_switch_enabled),
        )
        yield MenuItem(
            t("dlg_update_check_enabled"),
            self._queued(self._toggle_setting, "update_check_enabled"),
            checked=_flag(self.settings.update_check_enabled),
        )
        yield MenuItem(t("dlg_sound_menu"), Menu(*self._sound_items()))
        yield MenuItem(t("dlg_att_section"), Menu(*self._attention_options()))
        if costs.platform_supported() and costs.runner_available(
            self.settings.cost.command
        ):
            yield MenuItem(
                t("dlg_cost_enabled"),
                self._queued(self._toggle_cost_enabled),
                checked=_flag(self.settings.cost.enabled),
            )

        yield Menu.SEPARATOR
        yield MenuItem(
            t("win_startup"),
            self._queued(self._toggle_startup),
            checked=_flag(winshell.startup_enabled()),
        )

    def _attention_options(self):
        """The blink toggle, plus the hook registration it depends on."""
        if self._hooks_installed is None:
            # Menu build runs on the message-loop thread, so this has to stay
            # a small file read — same budget as the registry lookup the
            # startup row already does two rows below.
            self._refresh_hook_state()
        installed = self._hooks_installed
        yield MenuItem(
            t("dlg_att_enabled"),
            self._queued(self._toggle_attention),
            checked=_flag(self.settings.attention.enabled),
        )
        yield Menu.SEPARATOR
        yield MenuItem(
            t("dlg_att_hooks_ok") if installed else t("dlg_att_hooks_missing"),
            None,
            enabled=False,
        )
        if installed:
            yield MenuItem(
                t("dlg_att_hooks_remove"), self._queued(self._on_remove_hooks)
            )
        else:
            yield MenuItem(
                t("dlg_att_hooks_install"), self._queued(self._on_install_hooks)
            )

    def _sound_items(self):
        snd = self.settings.sound
        yield MenuItem(
            t("dlg_sound_enabled"),
            self._queued(self._toggle_sound_enabled),
            checked=_flag(snd.enabled),
        )
        yield Menu.SEPARATOR
        yield MenuItem(
            t("dlg_sound_mode"),
            Menu(
                *[
                    MenuItem(
                        t(f"dlg_sound_mode_{mode}"),
                        self._queued(self._set_sound_mode, mode),
                        checked=_flag(snd.mode == mode),
                        radio=True,
                    )
                    for mode in VALID_SOUND_MODES
                ]
            ),
        )
        yield MenuItem(t("dlg_sound_choose_audio"), self._queued(self._choose_custom, "audio"))
        yield MenuItem(t("dlg_sound_choose_image"), self._queued(self._choose_custom, "image"))
        yield Menu.SEPARATOR
        yield MenuItem(t("dlg_sound_test"), self._queued(self._test_sound))

    # ------------------------------------------------------------- settings

    def _save_and_apply(self, new: Settings) -> None:
        try:
            save_settings(new)
        except OSError as e:
            self.notify(t("dlg_save_failed_title"), str(e))
            return
        self.settings = new
        self.settings_mtime = settings_mtime()
        lang = detect_lang(self.settings.lang)
        if lang != current_lang():
            set_lang(lang)
            setup_locale(lang)
        self._apply_visuals(self._last_claude_state)

    def _toggle_setting(self, attr: str) -> None:
        self._save_and_apply(
            dataclasses.replace(self.settings, **{attr: not getattr(self.settings, attr)})
        )

    def _toggle_topbar(self, attr: str) -> None:
        tb = dataclasses.replace(
            self.settings.topbar, **{attr: not getattr(self.settings.topbar, attr)}
        )
        self._save_and_apply(dataclasses.replace(self.settings, topbar=tb))

    def _toggle_metric(self, metric: str) -> None:
        cur = list(self.settings.topbar.claude_metrics)
        if metric in cur:
            cur.remove(metric)
        else:
            cur.append(metric)
        tb = dataclasses.replace(self.settings.topbar, claude_metrics=tuple(cur))
        self._save_and_apply(dataclasses.replace(self.settings, topbar=tb))

    def _set_language(self, code: str) -> None:
        self._save_and_apply(dataclasses.replace(self.settings, lang=code))

    def _toggle_cost_enabled(self) -> None:
        cost = dataclasses.replace(
            self.settings.cost, enabled=not self.settings.cost.enabled
        )
        self._save_and_apply(dataclasses.replace(self.settings, cost=cost))
        if cost.enabled:
            if self.cost_report is None:
                self.cost_report = costs.cached()
            self._refresh_costs()

    def _toggle_attention(self) -> None:
        att = dataclasses.replace(
            self.settings.attention, enabled=not self.settings.attention.enabled
        )
        self._save_and_apply(dataclasses.replace(self.settings, attention=att))
        if not att.enabled:
            self._rest()
            return
        # Turning the blink on with no hooks registered would look broken —
        # nothing would ever flag a session. Say so rather than let the user
        # discover it by waiting.
        self._refresh_hook_state()
        if not self._hooks_installed:
            self.notify(t("dlg_att_section"), t("dlg_att_hooks_missing"))

    def _refresh_hook_state(self) -> None:
        self._hooks_installed = attention_hooks.installed()

    def _on_install_hooks(self) -> None:
        """Confirm, merge into ~/.claude/settings.json, then prove it works."""
        if not winshell.confirm(
            t("dlg_att_hooks_confirm_title"), t("dlg_att_hooks_confirm_body")
        ):
            return
        result = attention_hooks.install()
        self._refresh_hook_state()
        self.icon.update_menu()
        if not result.ok:
            self.notify(
                t("dlg_att_section"), t("dlg_att_hooks_failed", err=result.detail)
            )
            return
        # The hook only ever runs inside Claude Code's own shell, so a quoting
        # mistake would otherwise present as "it just never blinks".
        test = attention_hooks.selftest()
        self.notify(
            t("dlg_att_section"),
            t("dlg_att_hooks_ok")
            if test.ok
            else t("dlg_att_hooks_failed", err=test.detail),
        )

    def _on_remove_hooks(self) -> None:
        result = attention_hooks.uninstall()
        self._refresh_hook_state()
        self._on_clear_attention()
        if not result.ok:
            self.notify(
                t("dlg_att_section"), t("dlg_att_hooks_failed", err=result.detail)
            )

    def _toggle_sound_enabled(self) -> None:
        snd = dataclasses.replace(
            self.settings.sound, enabled=not self.settings.sound.enabled
        )
        self._save_and_apply(dataclasses.replace(self.settings, sound=snd))

    def _set_sound_mode(self, mode: str) -> None:
        snd = dataclasses.replace(self.settings.sound, mode=mode)
        self._save_and_apply(dataclasses.replace(self.settings, sound=snd))

    def _choose_custom(self, kind: str) -> None:
        """Pick a custom sound/image; picking one also selects custom mode."""
        spec = winshell.AUDIO_FILTER if kind == "audio" else winshell.IMAGE_FILTER
        path = winshell.choose_file(APP_TITLE, spec)
        if not path:
            return
        field = "custom_audio" if kind == "audio" else "custom_image"
        snd = dataclasses.replace(self.settings.sound, mode="custom", **{field: path})
        self._save_and_apply(dataclasses.replace(self.settings, sound=snd))

    def _test_sound(self) -> None:
        sound.preview(self.settings.sound, ASSETS_DIR)

    def _toggle_startup(self) -> None:
        """Add/remove the HKCU Run entry — the one setting that isn't in JSON."""
        target = not winshell.startup_enabled()
        if not winshell.set_startup(target, SCRIPT_PATH):
            self.notify(t("dlg_save_failed_title"), t("win_startup"))
        self._apply_visuals(self._last_claude_state)

    # ------------------------------------------------------------- actions

    def _on_login(self) -> None:
        if winshell.open_claude_login():
            self.notify(t("login_title_ok"), t("login_body_ok"))
        else:
            self.notify(t("login_title_fail"), t("login_body_fail"))

    def _on_refresh(self) -> bool:
        return True  # ask the loop for an immediate poll

    def _on_clear_alerts(self) -> None:
        self.active_alerts.clear()
        self._apply_visuals(self._last_claude_state)

    def _on_update(self) -> None:
        latest = updates.display(self.update_info.latest if self.update_info else None)
        if not winshell.confirm(
            t("update_confirm_title"), t("update_confirm_body", ver=latest)
        ):
            return
        if not winshell.run_script_in_console(INSTALL_DIR / UPDATE_SCRIPT):
            self.notify(t("update_spawn_fail_title"), t("win_update_manual"))

    def _handoff_cleared(self) -> bool:
        """Ask before switching under a live session. Worker thread only.

        A running ``claude`` keeps its token in memory, so the swap does not
        disturb it — but when that token expires the session writes its own
        credentials back and the switch is gone, with nothing to show for it.
        Hence a real question rather than a silent proceed, and the same
        question when the probe itself could not run.
        """
        probe = handoff.running_sessions()
        if not probe.risky:
            return True
        body = (
            t("ho_busy_body", n=probe.count)
            if probe.busy
            else t("ho_unknown_body")
        )
        return winshell.confirm(t("ho_busy_title"), f"{body}\n\n{t('ho_switch_anyway')} ?")

    def _on_switch(self, acct_id: str, email: str) -> bool:
        if not self._handoff_cleared():
            return False
        if not winshell.confirm(
            t("acct_switch_confirm_title"), t("acct_switch_confirm_body", email=email)
        ):
            return False
        if accounts.switch_to(acct_id, datetime.now(timezone.utc)):
            self.notify(t("acct_switch_ok_title"), t("acct_switch_ok_body", email=email))
        else:
            self.notify(t("acct_switch_fail_title"), t("acct_switch_fail_body"))
        return True

    def _on_switch_resume(self, acct_id: str, email: str, path: str) -> bool:
        """Switch account, then reopen that folder's last conversation.

        The order matters: the terminal must start *after* the credentials
        are in place, because ``claude`` reads them once at launch.
        """
        name = Path(path).name or path
        if not self._handoff_cleared():
            return False
        if not winshell.confirm(
            t("ho_resume_confirm_title", email=email),
            t("ho_resume_confirm_body", email=email, project=name),
        ):
            return False

        if not accounts.switch_to(acct_id, datetime.now(timezone.utc)):
            self.notify(t("acct_switch_fail_title"), t("acct_switch_fail_body"))
            return True

        argv = handoff.resume_argv()
        if argv is None:
            self.notify(t("ho_no_binary_title"), t("ho_no_binary_body"))
        elif winshell.spawn(argv, console=True, cwd=path):
            self.notify(
                t("ho_resume_ok_title"),
                t("ho_resume_ok_body", project=name, email=email),
            )
        else:
            self.notify(
                t("ho_launch_fail_title"), t("ho_launch_fail_body", project=name)
            )
        return True

    def _on_forget(self, acct_id: str, email: str) -> bool:
        if not winshell.confirm(
            t("acct_forget_confirm_title"), t("acct_forget_confirm_body", email=email)
        ):
            return False
        accounts.forget(acct_id)
        return True

    # -------------------------------------------------------------- updates

    def _check_update(self, force: bool) -> None:
        if not self.settings.update_check_enabled:
            self.update_info = None
            return
        info = updates.check(force=force)
        self.update_info = info
        if info.available and not updates.was_notified(info.latest):
            updates.mark_notified(info.latest)
            self.notify(
                t("update_notif_title"),
                t("update_notif_body", ver=updates.display(info.latest)),
            )
        self._apply_visuals(self._last_claude_state)


def _setup_logging() -> None:
    """Give stderr somewhere to land when running under ``pythonw.exe``.

    A windowless interpreter has no console, so ``sys.stderr`` is ``None`` —
    and ``print(..., file=None)`` falls through to a ``None`` stdout and
    silently does nothing. Nothing *crashes*, which is worse: every tick
    error, settings warning and ccusage failure in the shared modules would
    vanish. Autostart always launches us that way, so the daemon opens its
    own log instead.
    """
    if sys.stderr is not None:
        return  # a real console — leave it alone
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > LOG_MAX_BYTES:
            LOG_PATH.unlink()
        stream = open(LOG_PATH, "a", encoding="utf-8", errors="replace", buffering=1)
    except OSError:
        return  # unwritable log is not a reason to refuse to start
    sys.stderr = stream
    sys.stdout = stream
    print(f"--- {APP_TITLE} started {datetime.now():%Y-%m-%d %H:%M:%S} ---")


def main() -> int:
    if not winshell.is_windows():
        print(
            "claude_usage_tray is the Windows build. "
            "On Linux run claude_usage_indicator.py, on macOS "
            "claude_usage_menubar.py.",
            file=sys.stderr,
        )
        return 1
    _setup_logging()
    TrayApp().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
