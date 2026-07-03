# Claude Usage Tab

**See your Claude Code usage in the GNOME top bar — your 5-hour session and 7-day weekly limits, always one glance away.**

![version](https://img.shields.io/badge/version-1.0.0-blue)
![platform](https://img.shields.io/badge/platform-Linux%20·%20GNOME-informational)
![python](https://img.shields.io/badge/python-system%203-blue)
![license](https://img.shields.io/badge/license-MIT-green)

Same numbers as `claude /usage`, but you never have to open a terminal to
check how much budget you have left. A tiny always-on indicator sits in
your top bar, turns colour as you approach your limits, and can ping you
before you run out.

```
 C 17% . 5%        ← 5-hour session at 17%, weekly at 5%, right in your top bar
```

> [!NOTE]
> **Unofficial project — not affiliated with or endorsed by Anthropic.**
> It reads the same undocumented `/api/oauth/usage` endpoint that Claude
> Code's own `/usage` command calls. If Anthropic changes it, the
> indicator degrades gracefully (it won't crash) and may need an update.

<!-- TODO: add a screenshot of the top-bar label + open dropdown here, e.g. docs/screenshot.png -->

---

## Requirements

- **Linux with GNOME** (tested on Ubuntu and Fedora; anything GNOME-based
  should work).
- The **AppIndicator / KStatusNotifierItem** GNOME extension enabled
  (Ubuntu ships it; on Fedora you install it once — see below).
- **Claude Code** installed and logged in (`claude` at least once, so
  `~/.claude/.credentials.json` exists).

No virtualenv, no `pip install`, no build step — it runs on your system
Python and the GTK libraries your distro already ships.

---

## Quick start

```bash
git clone https://github.com/Skycun/claude-usage-tab.git ~/claude-usage-tab
cd ~/claude-usage-tab
./install.sh
```

That's it — the indicator appears in your top bar and starts polling. The
installer detects Ubuntu (`apt`) or Fedora (`dnf`), installs the handful
of system packages it needs, copies the icons, adds an app-grid entry,
and asks whether you want it to **start automatically at login**. It's
safe to re-run any time.

> **Not logged in to Claude Code yet?** Run `claude` once — it opens the
> OAuth login in your browser. The indicator picks up the token on its
> next tick, no restart needed.

On **Fedora**, the AppIndicator extension isn't in the repos — the
installer prints a reminder with this one-time link:
<https://extensions.gnome.org/extension/615/appindicator-support/>

<details>
<summary><b>Prefer to install the dependencies by hand?</b></summary>

**Ubuntu / Debian**
```bash
sudo apt install gir1.2-ayatanaappindicator3-0.1 python3-gi python3-requests libnotify-bin
gnome-extensions enable ubuntu-appindicators@ubuntu.com
```

**Fedora / RHEL**
```bash
sudo dnf install libayatana-appindicator-gtk3 python3-gobject python3-requests libnotify
# then enable: https://extensions.gnome.org/extension/615/appindicator-support/
```

Then run it directly:
```bash
/usr/bin/python3 ~/claude-usage-tab/claude_usage_indicator.py
```
The `/usr/bin/python3` path is deliberate: PyGObject comes from your
distro's `python3-gi` / `python3-gobject` package and usually isn't
present inside virtualenvs.
</details>

---

## Features

- 🟦 **Top-bar label** — `C 17% . 5%`, refreshed every 60 s, with a
  `/!\` prefix when a custom alert is firing.
- 🎨 **Colour-coded icon** — normal, **gray** when there's no active
  session or the endpoint is rate-limited, **pink** when you hit a limit.
- 📊 **Rich dropdown** — session + weekly bars with reset countdowns, the
  Sonnet sub-limit and extra-usage credits when present, your account
  email + plan, and a one-click refresh.
- 🎛️ **Customisable display** — choose which metrics show, labels vs bare
  percentages, compact mode, separators — all from a settings window with
  a live preview.
- 👥 **Multi-account switcher** — remembers every Claude account you sign
  in as, shows each one's usage, and (opt-in) switches which account
  Claude Code uses next. → [Multiple accounts](#multiple-accounts)
- 🔔 **Notifications** — when a window resets, when you cross 80 % / 95 %,
  and on your own custom rate alerts.
- ⬆️ **Built-in updates** — checks GitHub for a new release and updates in
  one click. → [Updates](#updates)
- 🌍 **Bilingual** — English (default) and French.
- 🛟 **Fails gracefully** — a missing token, an outage, or Anthropic's
  known `429` bug never crash it; stats stay on screen and it retries with
  backoff.

---

## Updates

The indicator checks GitHub for a newer release shortly after launch and
every 6 hours (an anonymous request — no token, nothing about you is
sent; you can turn it off under **Settings ▸ Maintenance**). When a newer
version exists you get:

- a one-time desktop notification,
- an **_Update available (vX.Y.Z)_** row in the menu, and
- a status line at the bottom of the settings window.

Click it (or **Update now** in **Settings ▸ Maintenance**) — it opens a
terminal, runs `git pull --ff-only` then reinstalls, keeping your
autostart choice. Your settings and history are untouched. Prefer the
command line? `./update.sh` does the same thing.

## Uninstall

From the app: **Settings ▸ Maintenance ▸ Uninstall the app…** (tick the
box to also wipe your data). Or from a terminal:

```bash
./uninstall.sh           # remove the app, keep settings & history
./uninstall.sh --purge   # also wipe settings, history and stored accounts
```

System packages and the GNOME extension are left in place (other apps may
use them).

---

## macOS (experimental)

There's a menu-bar build for macOS that reuses the same engine, driven by
[`rumps`](https://github.com/jaredks/rumps) instead of GTK:

```bash
git clone https://github.com/Skycun/claude-usage-tab.git ~/claude-usage-tab
cd ~/claude-usage-tab
./install-macos.sh
```

It's newer and needs validation on real hardware — see
[docs/macos.md](./docs/macos.md) for what's supported, the differences from
the Linux build, and the known caveats (Keychain, notifications).

---

## Settings

Open **Settings…** from the menu — a small GTK window with four tabs:

- **General** — language, refresh interval, alert thresholds.
- **Top-bar** — which metrics show (5h / 7d / Sonnet), the `C` prefix, the
  `/!\` alert prefix, per-metric labels, compact mode, the separator, and
  decimals. A **live preview** updates as you toggle.
- **Accounts** — enable multi-account tracking and, separately, the
  (invasive) account switch.
- **Maintenance** — version, update check on/off, **Update now**, and
  **Uninstall**.

Changes apply immediately — no restart. Everything is stored in
`~/.config/claude-usage-indicator/settings.json`, which you can also edit
by hand (invalid values fall back to defaults instead of crashing):

```jsonc
{
  "schema_version": 2,
  "lang": "en",                        // "en" or "fr"
  "poll_seconds": 60,                  // minimum 10
  "builtin_thresholds": [80, 95],      // notify at these % (0–100)
  "update_check_enabled": true,        // check GitHub for new releases
  "accounts_enabled": true,            // remember + show all your accounts
  "account_switch_enabled": false,     // opt-in: allow switching (writes ~/.claude)
  "topbar": {
    "show_claude": true,               // show the label at all
    "claude_metrics": ["five_hour", "seven_day"],  // + "seven_day_sonnet"
    "show_provider_prefix": true,      // the leading "C"
    "show_alert_prefix": true,         // the "/!\\" when an alert is active
    "metric_labels": false,            // prefix each value with 5h / 7d / S7
    "compact": false,                  // show only the highest value
    "metric_separator": " . ",         // between metrics (ASCII + · • – —)
    "percent_decimals": 0              // 0–2
  },
  "alerts": [
    {
      "id": "daily-burn",
      "enabled": false,
      "metric": "seven_day",           // five_hour | seven_day | seven_day_sonnet
      "delta_pp": 20,                  // fire when it grows > 20 percentage points…
      "window_hours": 12,              // …over this sliding window
      "cooldown_hours": 6,             // stay quiet this long after firing
      "label": "Fast weekly burn"
    }
  ]
}
```

`CLAUDE_USAGE_LANG=en|fr` overrides the file's `lang` (handy for testing).

### Custom rate alerts

An alert watches one metric over a sliding window and **fires** when
usage grows by at least `delta_pp` percentage points within
`window_hours`. When it fires you get a notification, a `/!\` prefix on
the label, and a **Clear alerts** item in the menu. Alerts re-arm when the
metric resets or when you clear them. There's a 5-minute quiet period at
startup while history builds up.

---

## Multiple accounts

Claude Code only stores **one** active account at a time — signing in as
another overwrites it. This app remembers all of them and lets you flip
between them.

- **Automatic capture.** With `accounts_enabled` on (default), each time
  you sign in as a different account the daemon snapshots it on the next
  tick. Nothing to click.
- **See every account's usage.** A **Claude accounts** submenu lists them
  all with their 5h / 7d usage; the active one is marked `●`. Inactive
  accounts are polled too (their token is refreshed automatically when it
  expires).
- **Switch (opt-in).** Turn on **Allow switching accounts** in the
  *Accounts* tab first — it's off by default because it **writes into
  `~/.claude`**. Then pick an account → **Switch to this account**. The
  app swaps only the credential blocks (backing up `~/.claude.json`
  first) and leaves everything else alone.

> Switching takes effect on the **next** `claude` launch — a running
> session won't switch mid-flight.

Stored accounts live in `~/.config/claude-usage-indicator/accounts/`
(`chmod 0600` — they contain OAuth tokens, so treat them like
`~/.claude/.credentials.json`). Remove one with **Forget this account**.

---

## Troubleshooting

**Nothing shows in the top bar**
```bash
gnome-extensions list --enabled | grep -i appindicator   # extension on?
pgrep -a -f claude_usage_indicator.py                     # daemon running?
tail -n 50 /tmp/claude_usage_indicator.log                # any errors?
```

**The icon is always gray** — either your 5-hour window is at 0 % (no
active session) or the endpoint is rate-limited. Open the menu: the
refresh line says *Rate-limited (retry in …)* in the second case.

**Check the endpoint directly**
```bash
bash test_claude_usage.sh
```
| Response | Meaning |
|---|---|
| `HTTP 200` + JSON | All good. |
| `HTTP 429` (`rate_limit_error`) | Known Anthropic bug ([#31021][issue-31021]) — handled: stats stay, icon greys, retries with backoff. |
| `HTTP 401` | Token expired — run `claude` to re-authenticate. |
| `no accessToken found` | Not logged in — run `claude`. |

---

## How it works

Every 60 seconds the daemon calls
`https://api.anthropic.com/api/oauth/usage` with the OAuth token from
`~/.claude/.credentials.json` (**never logged, never sent anywhere else**)
and reads back the `five_hour` / `seven_day` / `seven_day_sonnet` /
`extra_usage` utilisation. Your email and plan come from `~/.claude.json`.
A `429` is treated as the known Anthropic-side bug, not an error: stats
stay on screen, the icon greys, and it retries with exponential backoff
(2 → 5 → 15 → 30 → 60 min), honouring `Retry-After`.

### Project layout

```
claude_usage_indicator.py   # the indicator (UI + poll loop)
topbar.py                   # top-bar label composition
settings_dialog.py          # GTK settings window
settings.py / strings.py    # config schema + English/French strings
api.py                      # token read + usage fetch + token refresh
accounts.py                 # multi-account store + switch
alerts.py                   # usage history + custom-alert engine
updates.py / version.py     # GitHub release check + version
install.sh / update.sh / uninstall.sh
icons/                      # default · gray · full states
```

---

## Contributing

Issues and PRs are welcome. There's no build to set up — clone, edit,
and run `/usr/bin/python3 claude_usage_indicator.py` (see
[CLAUDE.md](./CLAUDE.md) for the design notes and the few
non-negotiables). New user-facing text must ship both English and French
strings.

## Upstream to watch

If Anthropic ships an official usage API/CLI, this project will migrate to
it and retire the undocumented-endpoint path.

- [claude-code #31021][issue-31021] — persistent `429`s on `/api/oauth/usage`.
- [claude-code #23975][issue-23975] — expose rate-limit data in the statusLine.
- [claude-code #44328][issue-44328] — proposal for an official `claude usage`.

[issue-31021]: https://github.com/anthropics/claude-code/issues/31021
[issue-23975]: https://github.com/anthropics/claude-code/issues/23975
[issue-44328]: https://github.com/anthropics/claude-code/issues/44328

## License

[MIT](./LICENSE) — do whatever you like, no warranty.
