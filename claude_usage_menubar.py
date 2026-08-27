#!/usr/bin/env python3
"""macOS menu-bar version of Claude Usage Tab.

The Linux app (``claude_usage_indicator.py``) speaks GTK/AppIndicator; this
one speaks the macOS menu bar via ``rumps`` (a thin wrapper over
``NSStatusItem``). Both reuse the exact same engine — ``api``, ``settings``,
``alerts``, ``accounts``, ``updates``, ``topbar``, ``strings`` and the
``formatting`` helpers — so only the presentation layer differs.

Run it directly (``python3 claude_usage_menubar.py``) or via the LaunchAgent
installed by ``install-macos.sh``. Requires ``rumps`` + ``requests``
(``pip install -r requirements-macos.txt``); PyObjC comes in with rumps.

Notifications and terminal actions use ``osascript`` so they work even when
run as a plain script (no app bundle / code-signing needed). Credentials are
read from ``~/.claude/.credentials.json`` and, if absent, the login Keychain
(handled in ``api.read_token``). The token is never logged.
"""

from __future__ import annotations

import dataclasses
import shlex
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import rumps

import accounts
import costs
import sound
import updates
from alerts import History, evaluate_alerts
from api import fetch_usage, parse_iso, read_account, read_token
from formatting import (
    fmt_secs,
    format_cost,
    format_local,
    format_remaining,
    format_stamp,
    progress_bar,
    to_float,
)
from settings import (
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
BACKOFF_STAGES = (120, 300, 900, 1800, 3600)
BOOT_COOLDOWN = timedelta(minutes=5)
HISTORY_SNAPSHOT_EVERY = 5
UPDATE_CHECK_DELAY = 5  # seconds after launch before the first update check
# A ccusage run happens on a worker thread, which must not touch AppKit. It
# parks its result and raises a flag; this main-loop timer is what re-renders
# the menu. The tick itself is a boolean check — cheap enough to run often.
COST_APPLY_INTERVAL = 5


# --------------------------------------------------------------- macOS shell-outs


def _osa_literal(text: str) -> str:
    """Quote a Python string as an AppleScript string literal."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def notify(summary: str, body: str) -> None:
    """Post a Notification Center banner via osascript (no bundle needed)."""
    script = (
        f"display notification {_osa_literal(body)} "
        f"with title {_osa_literal(summary)}"
    )
    try:
        subprocess.run(["osascript", "-e", script], check=False, timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass


def open_terminal(command: str) -> bool:
    """Open Terminal.app running ``command`` (detached)."""
    script = f"tell application \"Terminal\" to do script {_osa_literal(command)}"
    try:
        subprocess.Popen(["osascript", "-e", script], start_new_session=True)
        return True
    except OSError:
        return False


def open_path(target: str) -> None:
    """Open a URL or file with the default handler (``open``)."""
    try:
        subprocess.Popen(["open", target], start_new_session=True)
    except OSError:
        pass


_AUDIO_TYPES = '{"mp3","m4a","aiff","aif","wav","caf","aac"}'
_IMAGE_TYPES = '{"png","jpg","jpeg","gif","heic","bmp","tiff"}'


def choose_media_file(kind: str) -> str | None:
    """Native macOS file picker (osascript). Returns a POSIX path or None.

    ``kind`` is "audio" or "image". A user cancel (non-zero exit) yields None.
    """
    types = _AUDIO_TYPES if kind == "audio" else _IMAGE_TYPES
    script = (
        "POSIX path of (choose file with prompt "
        '"Claude Usage" of type ' + types + ")"
    )
    try:
        out = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None  # cancelled
    path = out.stdout.strip()
    return path or None


def spawn_claude_login() -> bool:
    return open_terminal("claude")


class ClaudeUsageApp(rumps.App):
    def __init__(self) -> None:
        # Resolve language before touching any UI strings, then let rumps
        # initialise before we assign our own attributes.
        settings = load_settings()
        lang = detect_lang(settings.lang)
        set_lang(lang)
        setup_locale(lang)

        super().__init__("Claude Usage", title=t("label_placeholder"), quit_button=None)

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
        self.boot_cooldown_until: datetime = (
            datetime.now(timezone.utc) + BOOT_COOLDOWN
        )
        self.update_info: updates.UpdateInfo | None = updates.cached()
        # API cost (ccusage), seeded from cache so the row has a number
        # before the first background run of the session finishes.
        self.cost_report: costs.CostReport | None = (
            costs.cached() if self.settings.cost.enabled else None
        )
        self._cost_thread: threading.Thread | None = None
        self._cost_dirty: bool = False
        self.account_states: list[dict] = []
        self._last_claude_state: dict | None = None
        # One-shot timers kept alive while a deferred render is pending.
        self._deferred: set = set()

        # Poll timer (re-created live if the interval changes).
        self._poll = rumps.Timer(self._tick, max(10, self.settings.poll_seconds))
        self._poll.start()
        # Update checks: one shortly after launch, then on a slow cadence.
        self._update_periodic = rumps.Timer(
            self._update_check_periodic, updates.PERIODIC_INTERVAL
        )
        self._update_periodic.start()
        self._update_startup = rumps.Timer(
            self._update_check_startup, UPDATE_CHECK_DELAY
        )
        self._update_startup.start()
        self._cost_apply = rumps.Timer(
            self._apply_cost_if_dirty, COST_APPLY_INTERVAL
        )
        self._cost_apply.start()

        self._tick()  # first render immediately

    # ------------------------------------------------------------------ tick

    def _tick(self, _timer: object = None) -> None:
        try:
            self._run_tick()
        except Exception as e:  # noqa: BLE001 — a bad tick must not kill the app
            print(f"tick error: {type(e).__name__}: {e}", file=sys.stderr)

    def _defer(self, fn) -> None:
        """Run ``fn`` on the next runloop turn instead of inline.

        Menu-item callbacks must not rebuild the menu (``menu.clear()``)
        synchronously from inside their own click dispatch — the GTK app
        defers the same work via ``GLib.idle_add`` for exactly this reason.
        A self-stopping one-shot ``rumps.Timer`` is the rumps equivalent; we
        keep a reference in ``self._deferred`` so it isn't GC'd before firing.
        """

        def _run(timer: object) -> None:
            try:
                timer.stop()
            except Exception:  # noqa: BLE001
                pass
            self._deferred.discard(timer)
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                print(f"deferred error: {type(e).__name__}: {e}", file=sys.stderr)

        timer = rumps.Timer(_run, 0.05)
        self._deferred.add(timer)
        timer.start()

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

        # Cost reads local transcripts, so it runs signed in or not.
        # ``costs.check`` throttles to the configured cadence — this tick
        # only gives it the opportunity.
        self._refresh_costs()

    def _reload_settings_if_needed(self) -> None:
        current = settings_mtime()
        if not current or current == self.settings_mtime:
            return
        old = self.settings
        self.settings = load_settings()
        self.settings_mtime = current
        new_lang = detect_lang(self.settings.lang)
        if new_lang != current_lang():
            set_lang(new_lang)
            setup_locale(new_lang)
        if old.poll_seconds != self.settings.poll_seconds:
            self._poll.stop()
            self._poll = rumps.Timer(
                self._tick, max(10, self.settings.poll_seconds)
            )
            self._poll.start()

    def _tick_claude(self, now: datetime) -> dict:
        if self.backoff_until and now < self.backoff_until:
            wait = int((self.backoff_until - now).total_seconds())
            return self._state("rate_limited", self.last_good, False, max(0, wait))

        data = fetch_usage(self.token)

        if data.get("rate_limited"):
            default_wait = BACKOFF_STAGES[
                min(self.backoff_idx, len(BACKOFF_STAGES) - 1)
            ]
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

    # -- accounts (ported from the GTK app; engine calls are shared) ------

    def _tick_accounts(
        self, now: datetime, claude_state: dict | None
    ) -> list[dict]:
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

    # ------------------------------------------------------------------ render

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
                notify(
                    t("alert_fired_title", label=f.label),
                    t("alert_fired_body", delta=f.delta, window=f.window_hours, metric=f.metric),
                )
                self.active_alerts[f.alert_id] = f.label
            if reset_metrics:
                to_drop = [
                    a.id
                    for a in self.settings.alerts
                    if a.id in self.active_alerts and a.metric in reset_metrics
                ]
                for aid in to_drop:
                    self.active_alerts.pop(aid, None)
            self.tick_counter += 1
            if self.tick_counter % HISTORY_SNAPSHOT_EVERY == 0:
                self.history.snapshot_to_disk()

        body, _guide = compose_label(
            claude_state, self.settings.topbar, bool(self.active_alerts)
        )
        if body is None:
            self.title = t("label_not_connected").strip()
        else:
            self.title = body.strip() or "C"

        self._render_menu(claude_state)

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

    def _detect_resets(
        self, triples: tuple[tuple[str, float, str], ...]
    ) -> set[str]:
        reset_metrics: set[str] = set()
        for metric, util, label in triples:
            prev = self.prev_util.get(metric)
            if prev is not None and util + 5 < prev:
                notify(t("reset_title", label=label), t("reset_body"))
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
                notify(
                    t("threshold_title", label=label, util=util),
                    t(
                        "threshold_body",
                        abs=format_local(reset),
                        rem=format_remaining(reset),
                    ),
                )
                # Configurable flourish on the 80% session (5 h) crossing.
                if metric == "five_hour" and threshold == 80:
                    sound.play_for(self.settings.sound, ASSETS_DIR)

    # -- menu rows -------------------------------------------------------

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
            t("session_line", bar=progress_bar(f_util), util=f_util, rem=format_remaining(f_reset)),
            t("weekly_line", bar=progress_bar(s_util), util=s_util, rem=format_remaining(s_reset)),
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

    def _render_menu(self, claude_state: dict | None) -> None:
        m = self.menu
        m.clear()
        data = (claude_state or {}).get("data") or {}

        m.add(rumps.MenuItem(read_account(), callback=lambda _s: open_path(SETTINGS_URL)))
        if not self.token:
            m.add(rumps.MenuItem(t("login_item"), callback=self._on_login))
        for line in self._metric_lines(data):
            m.add(rumps.MenuItem(line))  # no callback → disabled info row

        if self.settings.accounts_enabled and self.account_states:
            m.add(rumps.separator)
            parent = rumps.MenuItem(t("accounts_menu"))
            for st in self.account_states:
                self._add_account_item(parent, st)
            m.add(parent)

        if costs.is_available(self.settings.cost):
            m.add(rumps.separator)
            m.add(self._cost_menu())

        if self.active_alerts:
            m.add(rumps.separator)
            m.add(rumps.MenuItem("  ·  ".join(self.active_alerts.values())))
            m.add(rumps.MenuItem(t("clear_alerts"), callback=self._on_clear_alerts))

        m.add(rumps.separator)
        if (
            self.update_info
            and self.update_info.available
            and self.settings.update_check_enabled
        ):
            m.add(
                rumps.MenuItem(
                    t("update_available", ver=updates.display(self.update_info.latest)),
                    callback=self._on_update,
                )
            )
        m.add(rumps.MenuItem(self._refresh_label(claude_state), callback=self._on_refresh))
        m.add(self._options_menu())
        m.add(rumps.MenuItem(t("edit_settings"), callback=lambda _s: open_path(str(SETTINGS_PATH))))
        m.add(rumps.separator)
        m.add(rumps.MenuItem(f"v{updates.current_version()}"))
        m.add(rumps.MenuItem(t("quit"), callback=lambda _s: rumps.quit_application()))

    # -- API cost (ccusage) ----------------------------------------------

    def _cost_menu(self) -> rumps.MenuItem:
        """Summary row + breakdown submenu, built from ``self.cost_report``."""
        report = self.cost_report
        if report is None:
            # A runner exists (we're past is_available) but nothing has been
            # computed yet — say so rather than showing zeros, which would
            # read as "you spent nothing today".
            dash = t("dash")
            parent = rumps.MenuItem(t("cost_menu_pending"))
            rows = [
                t("cost_today", amount=dash),
                t("cost_7d", amount=dash),
                t("cost_month", amount=dash),
                t("cost_pending"),
            ]
        else:
            parent = rumps.MenuItem(
                t("cost_menu_title", amount=format_cost(report.today))
            )
            rows = [
                t("cost_today", amount=format_cost(report.today)),
                t("cost_7d", amount=format_cost(report.last_7d)),
                t("cost_month", amount=format_cost(report.month)),
                t("cost_stale")
                if report.stale
                else t("cost_checked", when=format_stamp(report.checked_at)),
            ]
        for line in rows:
            parent.add(rumps.MenuItem(line))  # no callback → disabled info row
        parent.add(
            rumps.MenuItem(t("cost_refresh"), callback=self._on_cost_refresh)
        )
        return parent

    def _refresh_costs(self, force: bool = False) -> None:
        """Kick a background ccusage run; a no-op while one is in flight.

        Never inline: the scan walks the whole transcript tree and would
        block the menu bar for seconds.
        """
        if not costs.is_available(self.settings.cost):
            return
        if self._cost_thread is not None and self._cost_thread.is_alive():
            return
        cost_settings = self.settings.cost

        def worker() -> None:
            try:
                report = costs.check(cost_settings, force=force)
            except Exception as e:  # noqa: BLE001 — a worker must not kill us
                print(
                    f"cost check error: {type(e).__name__}: {e}", file=sys.stderr
                )
                return
            # AppKit is off-limits here — park the result for the timer.
            self.cost_report = report
            self._cost_dirty = True

        self._cost_thread = threading.Thread(
            target=worker, name="ccusage", daemon=True
        )
        self._cost_thread.start()

    def _apply_cost_if_dirty(self, _timer: object = None) -> None:
        """Main-loop half of the worker handoff — re-render when new data landed."""
        if not self._cost_dirty:
            return
        self._cost_dirty = False
        try:
            self._render_menu(self._last_claude_state)
        except Exception as e:  # noqa: BLE001
            print(f"cost render error: {type(e).__name__}: {e}", file=sys.stderr)

    def _on_cost_refresh(self, _sender: object = None) -> None:
        self._refresh_costs(force=True)

    def _add_account_item(self, parent: rumps.MenuItem, st: dict) -> None:
        acct: accounts.Account = st["acct"]
        active = bool(st.get("active"))
        mark = t("acct_active_mark") if active else t("acct_inactive_mark")
        email = acct.email or acct.label
        plan = f" ({acct.plan})" if acct.plan else ""
        item = rumps.MenuItem(
            f"{mark} {email}{plan}  —  {self._account_usage_text(st)}"
        )
        if active:
            item.add(rumps.MenuItem(t("acct_switch_active")))
        elif not self.settings.account_switch_enabled:
            item.add(rumps.MenuItem(t("acct_switch_disabled")))
        else:
            item.add(
                rumps.MenuItem(
                    t("acct_switch"),
                    callback=lambda _s, aid=acct.id, em=email: self._on_switch(aid, em),
                )
            )
        if not active:
            item.add(
                rumps.MenuItem(
                    t("acct_forget"),
                    callback=lambda _s, aid=acct.id, em=email: self._on_forget(aid, em),
                )
            )
        parent.add(item)

    # -- settings submenu (checkable toggles → write settings.json) -------

    @staticmethod
    def _check_item(title: str, active: bool, callback) -> rumps.MenuItem:
        item = rumps.MenuItem(title, callback=callback)
        item.state = 1 if active else 0
        return item

    def _options_menu(self) -> rumps.MenuItem:
        opts = rumps.MenuItem(t("menu_options"))
        tb = self.settings.topbar

        lang_parent = rumps.MenuItem(t("dlg_lang"))
        for code in SUPPORTED_LANGS:
            lang_parent.add(
                self._check_item(
                    t(f"dlg_lang_{code}"),
                    current_lang() == code,
                    lambda _s, c=code: self._set_language(c),
                )
            )
        opts.add(lang_parent)

        opts.add(self._check_item(t("dlg_show_claude"), tb.show_claude, lambda _s: self._toggle_topbar("show_claude")))
        opts.add(self._check_item(t("dlg_compact"), tb.compact, lambda _s: self._toggle_topbar("compact")))
        opts.add(self._check_item(t("dlg_metric_labels"), tb.metric_labels, lambda _s: self._toggle_topbar("metric_labels")))
        opts.add(self._check_item(t("dlg_provider_prefix"), tb.show_provider_prefix, lambda _s: self._toggle_topbar("show_provider_prefix")))

        metrics = rumps.MenuItem(t("dlg_tab_topbar"))
        for key in VALID_CLAUDE_TOPBAR_METRICS:
            metrics.add(
                self._check_item(
                    t(f"dlg_metric_{key}"),
                    key in tb.claude_metrics,
                    lambda _s, k=key: self._toggle_metric(k),
                )
            )
        opts.add(metrics)

        opts.add(rumps.separator)
        opts.add(self._check_item(t("dlg_accounts_enabled"), self.settings.accounts_enabled, lambda _s: self._toggle_setting("accounts_enabled")))
        opts.add(self._check_item(t("dlg_account_switch_enabled"), self.settings.account_switch_enabled, lambda _s: self._toggle_setting("account_switch_enabled")))
        opts.add(self._check_item(t("dlg_update_check_enabled"), self.settings.update_check_enabled, lambda _s: self._toggle_setting("update_check_enabled")))

        snd = self.settings.sound
        sound_menu = rumps.MenuItem(t("dlg_sound_menu"))
        sound_menu.add(
            self._check_item(
                t("dlg_sound_enabled"),
                snd.enabled,
                lambda _s: self._toggle_sound_enabled(),
            )
        )
        sound_menu.add(rumps.separator)
        mode_menu = rumps.MenuItem(t("dlg_sound_mode"))
        for mode in VALID_SOUND_MODES:
            mode_menu.add(
                self._check_item(
                    t(f"dlg_sound_mode_{mode}"),
                    snd.mode == mode,
                    lambda _s, mo=mode: self._set_sound_mode(mo),
                )
            )
        sound_menu.add(mode_menu)
        sound_menu.add(
            rumps.MenuItem(
                t("dlg_sound_choose_audio"),
                callback=lambda _s: self._choose_custom("audio"),
            )
        )
        sound_menu.add(
            rumps.MenuItem(
                t("dlg_sound_choose_image"),
                callback=lambda _s: self._choose_custom("image"),
            )
        )
        sound_menu.add(rumps.separator)
        sound_menu.add(
            rumps.MenuItem(
                t("dlg_sound_test"), callback=lambda _s: self._test_sound()
            )
        )
        opts.add(sound_menu)
        # Only offer the toggle where ticking it would actually do something:
        # a validated platform with a runner installed. There is no settings
        # dialog here to explain an inert checkbox.
        if costs.platform_supported() and costs.runner_available(
            self.settings.cost.command
        ):
            opts.add(
                self._check_item(
                    t("dlg_cost_enabled"),
                    self.settings.cost.enabled,
                    lambda _s: self._toggle_cost_enabled(),
                )
            )
        return opts

    def _save_and_apply(self, new: Settings) -> None:
        try:
            save_settings(new)
        except OSError as e:
            notify(t("dlg_save_failed_title"), str(e))
            return
        self.settings = new
        self.settings_mtime = settings_mtime()
        lang = detect_lang(self.settings.lang)
        if lang != current_lang():
            set_lang(lang)
            setup_locale(lang)
        # Deferred re-render (settings already applied) — avoid rebuilding the
        # menu from inside the toggle's own click handler. No network hit.
        self._defer(
            lambda: self._render(self._last_claude_state, datetime.now(timezone.utc))
        )

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
        """Flip the cost readout. The command/cadence stay in settings.json."""
        cost = dataclasses.replace(
            self.settings.cost, enabled=not self.settings.cost.enabled
        )
        self._save_and_apply(dataclasses.replace(self.settings, cost=cost))
        if cost.enabled:
            # Show whatever the cache holds right away, then refresh.
            if self.cost_report is None:
                self.cost_report = costs.cached()
            self._refresh_costs()

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
        path = choose_media_file(kind)
        if not path:
            return
        field = "custom_audio" if kind == "audio" else "custom_image"
        snd = dataclasses.replace(self.settings.sound, mode="custom", **{field: path})
        self._save_and_apply(dataclasses.replace(self.settings, sound=snd))

    def _test_sound(self) -> None:
        sound.preview(self.settings.sound, ASSETS_DIR)

    # -- actions ---------------------------------------------------------

    def _on_login(self, _sender: object = None) -> None:
        if spawn_claude_login():
            notify(t("login_title_ok"), t("login_body_ok"))
        else:
            notify(t("login_title_fail"), t("login_body_fail"))

    def _on_refresh(self, _sender: object = None) -> None:
        # Deferred: don't rebuild the menu from inside its own click handler.
        self._defer(self._tick)

    def _on_clear_alerts(self, _sender: object = None) -> None:
        self.active_alerts.clear()
        self._defer(
            lambda: self._render(self._last_claude_state, datetime.now(timezone.utc))
        )

    def _on_update(self, _sender: object = None) -> None:
        latest = updates.display(self.update_info.latest if self.update_info else None)
        if rumps.alert(
            title=t("update_confirm_title"),
            message=t("update_confirm_body", ver=latest),
            ok=t("dlg_update_btn"),
            cancel=t("dlg_cancel"),
        ) != 1:
            return
        cmd = f"cd {shlex.quote(str(INSTALL_DIR))} && ./update-macos.sh"
        if not open_terminal(cmd):
            notify(t("update_spawn_fail_title"), t("update_spawn_fail_body"))

    def _on_switch(self, acct_id: str, email: str) -> None:
        if rumps.alert(
            title=t("acct_switch_confirm_title"),
            message=t("acct_switch_confirm_body", email=email),
            ok=t("acct_switch"),
            cancel=t("dlg_cancel"),
        ) != 1:
            return
        if accounts.switch_to(acct_id, datetime.now(timezone.utc)):
            notify(t("acct_switch_ok_title"), t("acct_switch_ok_body", email=email))
        else:
            notify(t("acct_switch_fail_title"), t("acct_switch_fail_body"))
        self._defer(self._tick)

    def _on_forget(self, acct_id: str, email: str) -> None:
        if rumps.alert(
            title=t("acct_forget_confirm_title"),
            message=t("acct_forget_confirm_body", email=email),
            ok=t("acct_forget"),
            cancel=t("dlg_cancel"),
        ) != 1:
            return
        accounts.forget(acct_id)
        self._defer(self._tick)

    # -- update checks ---------------------------------------------------

    def _update_check_startup(self, timer: object) -> None:
        try:
            timer.stop()
        except Exception:  # noqa: BLE001
            pass
        self._check_update(force=True)

    def _update_check_periodic(self, _timer: object) -> None:
        self._check_update(force=False)

    def _check_update(self, force: bool) -> None:
        if not self.settings.update_check_enabled:
            self.update_info = None
            self._render_menu(self._last_claude_state)
            return
        try:
            info = updates.check(force=force)
        except Exception as e:  # noqa: BLE001
            print(f"update check error: {type(e).__name__}: {e}", file=sys.stderr)
            return
        self.update_info = info
        if info.available and not updates.was_notified(info.latest):
            updates.mark_notified(info.latest)
            notify(
                t("update_notif_title"),
                t("update_notif_body", ver=updates.display(info.latest)),
            )
        self._render_menu(self._last_claude_state)


def main() -> int:
    ClaudeUsageApp().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
