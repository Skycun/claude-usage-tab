# claude-usage-tab

> GNOME AppIndicator (Ubuntu + Fedora) for **Claude Code** usage — your
> 5-hour session and 7-day weekly consumption, always visible in the top
> bar.

Same data as `claude /usage`, but glanceable. No need to open a terminal
to check how much budget you have left.

> [!NOTE]
> **Unofficial** — not affiliated with or endorsed by Anthropic. Uses the
> undocumented OAuth endpoint `/api/oauth/usage` that Claude Code's
> `/usage` command calls internally. May break if Anthropic changes the
> endpoint.

---

## Features

- **Top-bar label** — `5h 17%  ·  7j 5%`, updated every 60s. Prefixed
  with `/!\ ` when a custom alert is active.
- **Dynamic icon** — three states at a glance:
  - ⬛ *Default* — normal usage.
  - ⬜ *Gray* — no active session (5h window at 0%) **or** endpoint
    rate-limited (stale cache).
  - 🟪 *Full pink* — session or weekly limit reached (≥ 100%).
- **Rich dropdown menu**
  - Logged-in email and plan tier (click → opens
    `claude.ai/settings/usage`).
  - Session 5h + weekly 7j with progress bars and reset countdowns.
  - Sonnet-specific sub-limit when present.
  - Extra-usage credits when enabled.
  - Active alerts block + *Clear alerts* button when any are firing.
  - *Edit settings* item — opens `settings.json` in the default editor.
  - One-click refresh with last-update timestamp.
- **Bilingual UI** — French (default) and English. Switch with
  `CLAUDE_USAGE_LANG=en` or by editing `settings.json`.
- **Custom rate alerts** — e.g. "warn me when my weekly usage grows by
  more than 20 pp over a 12 h sliding window". Defined in
  `settings.json` (see [Settings](#settings)).
- **Desktop notifications**
  - When the 5h or weekly window resets.
  - When utilization crosses 80% and 95% (configurable).
  - When a custom rate alert fires.
- **Graceful degradation**
  - If the token is missing or expired: switches to a *"not connected"*
    layout with a *Connect* button that launches `claude` in a terminal.
  - If the endpoint returns `rate_limit_error` (a known Anthropic-side
    bug — see [claude-code #31021][issue-31021]): keeps the last-known
    stats on screen, switches to the gray icon, retries with exponential
    backoff (2min → 5min → 15min → 30min → 1h). Honours `Retry-After`
    when the server sends it.

---

## How it works

Polls `https://api.anthropic.com/api/oauth/usage` every 60 seconds with
the OAuth access token from `~/.claude/.credentials.json` (never logged,
never sent anywhere else). The endpoint returns:

```json
{
  "five_hour": { "utilization": 17.0, "resets_at": "..." },
  "seven_day": { "utilization": 5.0,  "resets_at": "..." },
  "seven_day_sonnet": { ... },
  "extra_usage": { ... }
}
```

Account email and plan tier are read from `~/.claude.json`
(`oauthAccount.emailAddress`, `claudeAiOauth.rateLimitTier`).

[issue-31021]: https://github.com/anthropics/claude-code/issues/31021

---

## Install

### Quick install (Ubuntu + Fedora)

```bash
git clone git@github.com:Skycun/claude-usage-tab.git ~/Projects/claude-usage-tab
cd ~/Projects/claude-usage-tab
./install.sh
```

The script reads `/etc/os-release`, picks `apt` or `dnf`, installs the
system packages, copies the icons to `~/.local/share/claude-usage-indicator/`,
and drops a `.desktop` file in `~/.config/autostart/` so the indicator
starts on every login. It is safe to re-run.

On Fedora the GNOME **AppIndicator and KStatusNotifierItem Support**
extension is not packaged — install it manually from
<https://extensions.gnome.org/extension/615/appindicator-support/>. The
script prints a reminder if it is not enabled.

### Manual install

<details><summary>Ubuntu / Debian</summary>

```bash
sudo apt install \
  gir1.2-ayatanaappindicator3-0.1 \
  python3-gi \
  python3-requests \
  libnotify-bin
gnome-extensions enable ubuntu-appindicators@ubuntu.com
```

</details>

<details><summary>Fedora / RHEL</summary>

```bash
sudo dnf install \
  libayatana-appindicator-gtk3 \
  python3-gobject \
  python3-requests \
  libnotify
```

Then enable the AppIndicator extension from
<https://extensions.gnome.org/extension/615/appindicator-support/>.

</details>

Then clone and run:

```bash
git clone git@github.com:Skycun/claude-usage-tab.git ~/Projects/claude-usage-tab
cd ~/Projects/claude-usage-tab
/usr/bin/python3 claude_usage_indicator.py
```

> The script uses **system Python** (`/usr/bin/python3`) because
> PyGObject is provided by the distro package (`python3-gi` on Debian,
> `python3-gobject` on Fedora) and isn't available in most virtualenvs.

### Log in to Claude Code (if you haven't)

```bash
claude       # triggers OAuth login in your browser
```

Once logged in, `~/.claude/.credentials.json` is created and the
indicator will pick up the token on the next tick.

---

## Launch

`./install.sh` starts the indicator for you at the end of the install,
so after the first run there is nothing else to do.

To launch it later (after a reboot or an explicit stop):

* **From the app grid** — press <kbd>Super</kbd>, type "Claude Usage
  Tab", press <kbd>Enter</kbd>. An entry is installed into
  `~/.local/share/applications/` so the indicator behaves like any
  other app.
* **From a terminal** — one line:

  ```bash
  setsid /usr/bin/python3 ~/Projects/claude-usage-tab/claude_usage_indicator.py \
    > /tmp/claude_usage_indicator.log 2>&1 < /dev/null &
  disown
  ```

Stop it:

```bash
pkill -f claude_usage_indicator.py
```

Tail the logs:

```bash
tail -f /tmp/claude_usage_indicator.log
```

---

## Autostart on login

`./install.sh` sets this up for you — it writes
`~/.config/autostart/claude-usage-indicator.desktop` pointing at the
repo directory. If you installed manually, copy
[`claude-usage-indicator.desktop`](./claude-usage-indicator.desktop) to
`~/.config/autostart/` and replace `@INSTALL_DIR@` with the absolute
path to your clone.

---

## Troubleshooting

### Test the OAuth endpoint directly

```bash
bash test_claude_usage.sh
```

| Response | Meaning |
|---|---|
| `HTTP 200` + JSON with `five_hour` / `seven_day` | All good. |
| `HTTP 429` with `rate_limit_error` | Known Anthropic bug (#31021). The indicator handles it — stats stay visible, icon turns gray, retries with backoff. |
| `HTTP 401` | Token expired. Run `claude` to re-authenticate. |
| `ERROR: no accessToken found` | Not logged in. Run `claude`. |

### Nothing appears in the top bar

- Check the extension: `gnome-extensions list --enabled | grep -i appindicator`
- Check the process: `pgrep -a -f claude_usage_indicator.py`
- Check logs: `tail -n 50 /tmp/claude_usage_indicator.log`

### Icon is always gray

Either your 5-hour window is at 0% (no active session) **or** the
endpoint is rate-limited. Open the menu — the refresh line will say
`Rate-limited (retry dans …)` in the second case.

---

## Settings

The daemon writes `~/.config/claude-usage-indicator/settings.json` on
first run. Edit it to tweak behaviour — changes are picked up on the
next tick (no restart required). Invalid JSON or invalid fields are
logged to stderr and the defaults are kept in memory.

```jsonc
{
  "schema_version": 1,
  "lang": "fr",              // "fr" or "en"
  "poll_seconds": 60,        // minimum 10
  "builtin_thresholds": [80, 95],
  "alerts": [
    {
      "id": "daily-burn",
      "enabled": false,
      "metric": "seven_day",       // five_hour | seven_day | seven_day_sonnet
      "delta_pp": 20,              // trigger when delta > 20 percentage points
      "window_hours": 12,          // sliding window size
      "cooldown_hours": 6,         // silence window after firing
      "label": "Conso hebdo rapide"
    }
  ]
}
```

### Custom rate alerts

Each alert watches one metric over a sliding time window. An alert
**fires** when utilisation has grown by at least `delta_pp` percentage
points over the last `window_hours`. When it fires:

- A desktop notification is sent with the alert's `label`.
- The top-bar label gets a `/!\ ` prefix.
- A *Clear alerts* item appears in the menu (you can also wait — the
  prefix disappears automatically when the metric resets or after
  `cooldown_hours` if you edit the alert to disable it).

Alerts are re-armed when the metric itself resets (drop > 5 pp) or when
you hit *Clear alerts*. There's a 5-minute quiet period at startup so
history can build up.

Env var `CLAUDE_USAGE_LANG=fr|en` overrides the file's `lang` value —
useful when testing.

## Project layout

```
claude-usage-tab/
├── claude_usage_indicator.py   # Indicator class + main()
├── strings.py                  # i18n (FR + EN)
├── settings.py                 # settings.json schema + loader
├── api.py                      # token + /api/oauth/usage fetcher
├── alerts.py                   # usage history + alert engine
├── test_claude_usage.sh        # one-shot endpoint tester
├── icons/
│   ├── claude.png              # default state
│   ├── gray-claude.png         # no-session / rate-limited state
│   └── full-claude.png         # limit-hit state
├── README.md
└── .gitignore
```

Icons are resolved in this order:

1. `~/.local/share/claude-usage-indicator/*.png` (user override).
2. `icons/*.png` (bundled with the repo).
3. GNOME `dialog-information-symbolic` fallback.

---

## Related issues & requests upstream

- [claude-code #31021][issue-31021] — `/api/oauth/usage` returns
  persistent 429s.
- [claude-code #23975][issue-23975] — expose rate-limit data in
  statusLine JSON.
- [claude-code #44328][issue-44328] — proposal for an official
  `claude usage` CLI / API.

If Anthropic ships a stable, documented endpoint, this project will
migrate to it.

[issue-23975]: https://github.com/anthropics/claude-code/issues/23975
[issue-44328]: https://github.com/anthropics/claude-code/issues/44328

---

## License

Personal project. Pick a license before making the repo public.
