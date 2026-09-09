# Claude Usage Tab

**See your Claude Code usage without leaving your desktop — your 5-hour session and 7-day weekly limits, always one glance away, in the GNOME top bar, the macOS menu bar or the Windows notification area.**

![version](https://img.shields.io/badge/version-1.3.0-blue)
![platform](https://img.shields.io/badge/platform-Linux%20·%20macOS%20·%20Windows-informational)
![python](https://img.shields.io/badge/python-system%203%20·%203.9%2B%20venv-blue)
![license](https://img.shields.io/badge/license-MIT-green)

Same numbers as `claude /usage`, but you never have to open a terminal to
check how much budget you have left. A tiny always-on indicator sits in
your bar, turns colour as you approach your limits, and can ping you
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

## Platforms

One engine, three shells — the polling, the alerts, the accounts and the
settings file are identical everywhere; only the presentation layer differs.

| | Where it lives | Built on | Install | Status |
|---|---|---|---|---|
| **Linux** | GNOME top bar | GTK + AppIndicator, system Python | `./install.sh` | stable |
| **macOS** | menu bar | [`rumps`](https://github.com/jaredks/rumps), venv | `./install-macos.sh` | experimental |
| **Windows** | notification area | [`pystray`](https://github.com/moses-palmer/pystray) + Pillow, venv | `.\install-windows.ps1` | experimental |

Jump to [macOS](#macos-experimental) or [Windows](#windows-experimental) —
the Requirements and Quick start below describe the **Linux** build.

---

## Requirements (Linux)

- **Linux with GNOME** (tested on Ubuntu and Fedora; anything GNOME-based
  should work).
- The **AppIndicator / KStatusNotifierItem** GNOME extension enabled
  (Ubuntu ships it; on Fedora you install it once — see below).
- **Claude Code** installed and logged in (`claude` at least once, so
  `~/.claude/.credentials.json` exists).

No virtualenv, no `pip install`, no build step — it runs on your system
Python and the GTK libraries your distro already ships.

---

## Quick start (Linux)

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

## Windows (experimental)

There's a **system-tray** build for Windows — the notification area at the
bottom-right of the taskbar — driven by
[`pystray`](https://github.com/moses-palmer/pystray) + Pillow:

```powershell
git clone https://github.com/Skycun/claude-usage-tab.git $HOME\claude-usage-tab
cd $HOME\claude-usage-tab
.\install-windows.ps1
```

The tray gives you an icon and a tooltip, and nothing else — so the number
*is* the icon: your utilisation is drawn into a coloured tile (Claude
terracotta, amber from 80%, red from 95%, grey when the reading is stale),
the tooltip holds the detail, and right-click opens the same dropdown as the
other platforms.

```
 [42]              <- the tray icon itself, in your notification area
```

Also new and needing validation on real hardware — see
[docs/windows.md](./docs/windows.md) for the install details, autostart,
logs, and the known caveats.

---

## Features

- 🟦 **Always-on readout** — `C 17% . 5%`, refreshed every 60 s, with a
  `/!\` prefix when a custom alert is firing. In the GNOME top bar, the
  macOS menu bar, or — on Windows, where the tray has no text — drawn
  straight into the icon.
- 🎨 **Colour-coded icon** — normal, **gray** when there's no active
  session or the endpoint is rate-limited, **pink** when you hit a limit
  (on Windows the tile itself goes amber at 80 %, red at 95 %).
- 📊 **Rich dropdown** — session + weekly bars with reset countdowns, the
  Sonnet sub-limit and extra-usage credits when present, your account
  email + plan, and a one-click refresh.
- 🎛️ **Customisable display** — choose which metrics show, labels vs bare
  percentages, compact mode, separators — from a GTK settings window with
  a live preview on Linux, an **Options** submenu on macOS and Windows.
- 💸 **Daily API cost** *(opt-in)* — what today's Claude Code traffic would
  have cost on the API, plus rolling 7 days and this month, computed
  locally by [ccusage](https://github.com/ryoppippi/ccusage). →
  [API cost](#api-cost)
- 🔴 **Blink when a terminal wants you** *(opt-in)* — the icon pulses as
  soon as one of your Claude Code sessions finishes its turn or stops on a
  permission prompt, and stops the moment you answer it. →
  [Terminal attention](#terminal-attention)
- 🔁 **Switch and carry on** — hit the 5 h limit? One click moves every open
  terminal to another account, and the app offers it to you the moment you're
  blocked. → [Multiple accounts](#multiple-accounts)
- 👥 **Multi-account switcher** — remembers every Claude account you sign
  in as, shows each one's usage, and (opt-in) switches which account
  Claude Code uses next. → [Multiple accounts](#multiple-accounts)
- 🔔 **Notifications** — when a window resets, when you cross 80 % / 95 %,
  and on your own custom rate alerts.
- ⬆️ **Built-in updates** — checks GitHub for a new release and updates in
  one click. → [Updates](#updates)
- 🌍 **Multilingual** — English (default), French, Spanish, German,
  Japanese and Portuguese (BR).
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
command line? `./update.sh` (Linux), `./update-macos.sh` or
`.\update-windows.ps1` do the same thing.

## Uninstall

From the app: **Settings ▸ Maintenance ▸ Uninstall the app…** (tick the
box to also wipe your data). Or from a terminal:

```bash
./uninstall.sh           # Linux — remove the app, keep settings & history
./uninstall.sh --purge   # also wipe settings, history and stored accounts

./uninstall-macos.sh     # macOS — same, --purge available
```
```powershell
.\uninstall-windows.ps1          # Windows — remove the app, keep your data
.\uninstall-windows.ps1 -Purge   # also wipe settings, history and accounts
```

System packages and the GNOME extension are left in place (other apps may
use them), and nothing under `~/.claude` is ever touched.

---

## Settings

Open **Settings…** from the menu. On **Linux** that's a small GTK window
with four tabs:

- **General** — language, refresh interval, alert thresholds, and the
  opt-in [API cost](#api-cost) readout.
- **Top-bar** — which metrics show (5h / 7d / Sonnet), the `C` prefix, the
  `/!\` alert prefix, per-metric labels, compact mode, the separator, and
  decimals. A **live preview** updates as you toggle.
- **Accounts** — enable multi-account tracking and, separately, the
  (invasive) account switch.
- **Maintenance** — version, update check on/off, **Update now**, and
  **Uninstall**.

On **macOS** and **Windows** there is no GTK window: the everyday toggles
live in a checkable **Options** submenu, and **Settings…** opens
`settings.json` in your default editor for the rest.

Changes apply immediately — no restart. Everything is stored in
`~/.config/claude-usage-indicator/settings.json`
(`%USERPROFILE%\.config\claude-usage-indicator\settings.json` on Windows —
same layout on all three), which you can also edit by hand (invalid values
fall back to defaults instead of crashing):

```jsonc
{
  "schema_version": 4,
  "lang": "en",                        // en · fr · es · de · ja · pt
  "poll_seconds": 60,                  // minimum 10
  "builtin_thresholds": [80, 95],      // notify at these % (0–100)
  "update_check_enabled": true,        // check GitHub for new releases
  "accounts_enabled": true,            // remember + show all your accounts
  "account_switch_enabled": false,     // opt-in: allow switching (writes ~/.claude)
  "cost": {
    "enabled": false,                  // opt-in: API cost row in the dropdown
    "command": "",                     // "" = auto-detect ccusage / bunx / npx
    "refresh_minutes": 15              // minimum 1 — a scan is expensive
  },
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

`CLAUDE_USAGE_LANG=en|fr|es|de|ja|pt` overrides the file's `lang` (handy
for testing).

### Custom rate alerts

An alert watches one metric over a sliding window and **fires** when
usage grows by at least `delta_pp` percentage points within
`window_hours`. When it fires you get a notification, a `/!\` prefix on
the label, and a **Clear alerts** item in the menu. Alerts re-arm when the
metric resets or when you clear them. There's a 5-minute quiet period at
startup while history builds up.

---

## API cost

Claude Code writes every request it makes to
`~/.claude/projects/**/*.jsonl`. [ccusage](https://github.com/ryoppippi/ccusage)
reads those transcripts and prices them, which answers a question the plan
limits can't: **what would today's usage have cost on the API?**

Turn it on in **Settings ▸ General ▸ API cost** (Linux and Windows — the
macOS menu-bar build gets it once the path has been exercised on a real
Mac). The dropdown then grows an
**API cost — $12.40 today** row, with today / last 7 days / this month
behind it, plus a **Recalculate now** action.

```
API cost — $12.40 today  ▸   Today: $12.40
                             Last 7 days: $86.10
                             This month: $214.75
                             Updated Mon 24 Aug 16:13
                             Recalculate now
```

**What you need.** Nothing extra if you already have
[bun](https://bun.sh), Node or [pnpm](https://pnpm.io): the indicator runs
`ccusage` if it's installed, otherwise `bunx ccusage@latest`, `npx -y
ccusage@latest` or `pnpm dlx ccusage@latest` — whichever it finds. It looks
in the usual per-user install dirs (`~/.bun/bin`, nvm, corepack,
`~/.local/share/pnpm`, and on Windows `%APPDATA%\npm`,
`%LOCALAPPDATA%\pnpm`, Volta…), not just `PATH`. If none of them exists the
row simply doesn't appear — the settings window tells you why when you
tick the box. Set `cost.command` to override (a pinned version, a
wrapper script…).

**Good to know**

- The figure is an **estimate**, not a bill. It's what the tokens would
  cost at API rates — your Pro/Max subscription is unaffected.
- ccusage counts **every CLI agent** it finds in your transcripts, so the
  total can include tools other than Claude Code.
- Recomputing walks your whole transcript tree (hundreds of MB is normal),
  so it runs on a background thread every `refresh_minutes` (15 by
  default), never on the 60 s usage poll.
- Cached in `~/.cache/claude-usage-indicator/costs.json` — dates and
  amounts only, no project names, no session ids.

---

## Terminal attention

When you run Claude Code in several terminals, the one that finished three
minutes ago is invisible until you go looking. Turn this on and the icon
blinks instead.

**Two states, told apart on sight:**

| What happened | Colour (Windows) | Marker (GNOME / macOS) | Rhythm |
|---|---|---|---|
| A session is waiting for an answer — a permission prompt, or a minute of silence | blue | `!` | fast |
| A session finished its turn | green | `*` | half as fast |

Waiting always wins: it is the state that is actually blocking work.

**Turning it on**

1. Open **Options ▸ Claude terminals** (the settings window on Linux).
2. Click **Install the Claude Code hooks**, and confirm.
3. Tick **Blink when a terminal wants me**.

**What the hooks do**

The blink is fed by four Claude Code hooks added to
`~/.claude/settings.json`: `Stop` and `Notification` raise a flag for the
session, `UserPromptSubmit` and `SessionEnd` clear it. So the blink stops on
its own the moment you type your next prompt in that terminal — you never
have to dismiss it. The dropdown lists which project each waiting terminal
belongs to, with a **Stop blinking** row if you want to silence them all.

**Good to know**

- Your existing hooks are left alone. The merge is additive, and the file is
  backed up to `settings.json.cusi-bak` first. **Remove the Claude Code
  hooks** takes out only the entries this app added.
- **On Windows, an icon hidden in the overflow chevron can't be seen
  blinking.** Drag it onto the taskbar first.
- Flags live in `~/.cache/claude-usage-indicator/attention/` and hold the
  project folder's name, never its path. A flag nothing cleared (a terminal
  killed outright) expires after an hour.
- No hooks installed means no flags, which means no blink — never an error.

---

## Multiple accounts

Claude Code only stores **one** active account at a time — signing in as
another overwrites it. This app remembers all of them and lets you flip
between them.

- **Automatic capture.** With `accounts_enabled` on (default), each time
  you sign in as a different account the daemon snapshots it on the next
  tick. Nothing to click.
- **See every account's usage.** A **Claude accounts** submenu lists them
  all with their 5 h / 7 d usage *and how long until each window resets*
  (`5h 100% ↳ 51min · 7j 11% ↳ 6j 17h`); the active one is marked `●`.
  Inactive accounts are polled too (their token is refreshed automatically
  when it expires). The countdown is the point: a percentage tells you that
  you're stuck, the countdown tells you whether to wait or to switch.
- **Switch (opt-in).** Turn on **Allow switching accounts** in the
  *Accounts* tab first — it's off by default because it **writes into
  `~/.claude`**. Then pick an account → **Switch to this account**. The
  app swaps only the credential blocks (backing up `~/.claude.json`
  first) and leaves everything else alone.

> Switching takes effect on the **next** `claude` launch — a running
> session won't switch mid-flight.

### Switch and carry on

You hit the 5 h limit mid-task and your other account still has room.

**The app offers it.** At 100 % on the 5 h window, if another stored account
is below 90 %, you get a notification and a row at the top of the menu:
*Limit reached — switch to …*. One click and it's done. No digging through
submenus at the moment you're blocked.

**Your open terminals come with you.** This is the part that makes it worth
doing: Claude Code re-reads its credentials while it runs, so a switch moves
the sessions you already have open, not just the next one you start. Nothing
is relaunched and no window is opened. Your conversation carries on where it
was, on the other account's quota.

Before switching, the app counts the running `claude` processes and tells you
how many are about to change account. That's information, not a barrier: the
default answer is yes. If the check can't run, it says so rather than
pretending nothing is open.

> This relies on undocumented Claude Code behaviour, the same way the usage
> figures do. It was verified by observation, not promised by anyone. If a
> future version pins credentials at startup, the switch would apply to your
> next launch only.

One judgement call is yours: rotating accounts to keep working past a limit
sits in a grey area of Anthropic's usage policy.

Stored accounts live in `~/.config/claude-usage-indicator/accounts/`
(`chmod 0600` — they contain OAuth tokens, so treat them like
`~/.claude/.credentials.json`). Remove one with **Forget this account**.

---

## Troubleshooting

**Nothing shows in the top bar** *(Linux)*
```bash
gnome-extensions list --enabled | grep -i appindicator   # extension on?
pgrep -a -f claude_usage_indicator.py                     # daemon running?
tail -n 50 /tmp/claude_usage_indicator.log                # any errors?
```

**Nothing shows in the menu bar** *(macOS)* — the app runs from a
LaunchAgent; check it is loaded and read its log
(see [docs/macos.md](./docs/macos.md)):
```bash
launchctl list | grep claude-usage-tab
tail -n 50 ~/Library/Logs/claude-usage-tab.log
```

**No tray icon** *(Windows)* — Windows hides new icons behind the `^`
chevron: click it and drag the Claude tile onto the taskbar. Autostart runs
under `pythonw.exe`, which has no console, so errors go to a file:
```powershell
Get-Content -Wait "$env:LOCALAPPDATA\claude-usage-indicator\tray.log"
```

**The icon is always gray** — either your 5-hour window is at 0 % (no
active session) or the endpoint is rate-limited. Open the menu: the
refresh line says *Rate-limited (retry in …)* in the second case.

**Check the endpoint directly** *(Linux / macOS — the script is bash)*
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
claude_usage_indicator.py   # Linux front-end (GTK/AppIndicator)
claude_usage_menubar.py     # macOS front-end (rumps)
claude_usage_tray.py        # Windows front-end (pystray)
topbar.py                   # top-bar label composition
formatting.py               # shared render helpers (durations, bars, coercion)
settings_dialog.py          # GTK settings window
settings.py / strings.py    # config schema + 6 language tables
api.py                      # token read + usage fetch + token refresh
accounts.py                 # multi-account store + switch
alerts.py                   # usage history + custom-alert engine
updates.py / version.py     # GitHub release check + version
costs.py                    # daily API cost via ccusage (opt-in)
sound.py                    # 80% flourish, all three platforms
attention.py                # Claude Code hook + flag store (terminal attention)
attention_hooks.py          # registers those hooks in ~/.claude/settings.json
handoff.py                  # running-session probe + when to offer a switch
trayicon.py                 # Windows: draws the percentage badge (Pillow)
winshell.py                 # Windows: dialogs, autostart, launching (ctypes)
install.sh / update.sh / uninstall.sh                  # Linux
install-macos.sh / update-macos.sh / uninstall-macos.sh
install-windows.ps1 / update-windows.ps1 / uninstall-windows.ps1
icons/                      # default · gray · full states
```

---

## Contributing

Issues and PRs are welcome. There's no build to set up — clone, edit, and
run the front-end for your platform:

```bash
/usr/bin/python3 claude_usage_indicator.py        # Linux (system Python)
.venv/bin/python3 claude_usage_menubar.py         # macOS
.\.venv\Scripts\python.exe claude_usage_tray.py   # Windows
```

Logic belongs in the shared modules (`api`, `settings`, `alerts`,
`accounts`, `updates`, `costs`, `topbar`, `strings`, `formatting`,
`sound`) so a fix lands once and every platform gets it — see
[CLAUDE.md](./CLAUDE.md) for the design notes and the few
non-negotiables. New user-facing text must be added to **all six**
language tables in `strings.py` (EN, FR, ES, DE, JA, PT).

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
