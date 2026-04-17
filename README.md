# claude-usage-tab

> Ubuntu / GNOME AppIndicator for **Claude Code** usage — your 5-hour
> session and 7-day weekly consumption, always visible in the top bar.

Same data as `claude /usage`, but glanceable. No need to open a terminal
to check how much budget you have left.

> [!NOTE]
> **Unofficial** — not affiliated with or endorsed by Anthropic. Uses the
> undocumented OAuth endpoint `/api/oauth/usage` that Claude Code's
> `/usage` command calls internally. May break if Anthropic changes the
> endpoint.

---

## Features

- **Top-bar label** — `5h 17%  ·  7j 5%`, updated every 60s.
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
  - One-click refresh with last-update timestamp.
- **Desktop notifications**
  - When the 5h or weekly window resets.
  - When utilization crosses 80% and 95%.
- **Graceful degradation**
  - If the token is missing or expired: switches to a *“not connected”*
    layout with a *Connect* button that launches `claude` in a terminal.
  - If the endpoint returns `rate_limit_error` (a known Anthropic-side
    bug — see [claude-code #31021][issue-31021]): keeps the last-known
    stats on screen, switches to the gray icon, retries with exponential
    backoff (2min → 5min → 15min → 30min → 1h).

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

### 1. System dependencies

```bash
sudo apt install \
  gir1.2-ayatanaappindicator3-0.1 \
  python3-gi \
  python3-requests \
  libnotify-bin
```

You also need the **AppIndicator and KStatusNotifierItem Support**
GNOME extension enabled (it ships with Ubuntu by default). If the icon
doesn't appear:

```bash
gnome-extensions enable ubuntu-appindicators@ubuntu.com
```

### 2. Clone and run

```bash
git clone git@github.com:Skycun/claude-usage-tab.git ~/Projects/claude-usage-tab
cd ~/Projects/claude-usage-tab
/usr/bin/python3 claude_usage_indicator.py
```

> The script uses **system Python** (`/usr/bin/python3`) because
> PyGObject is provided by the Ubuntu `python3-gi` package and isn't
> available in most virtualenvs.

### 3. Log in to Claude Code (if you haven't)

```bash
claude       # triggers OAuth login in your browser
```

Once logged in, `~/.claude/.credentials.json` is created and the
indicator will pick up the token on the next tick.

---

## Run in the background

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

Drop a `.desktop` file in `~/.config/autostart/`:

```ini
[Desktop Entry]
Type=Application
Name=Claude Usage Tab
Exec=/usr/bin/python3 /home/YOUR_USER/Projects/claude-usage-tab/claude_usage_indicator.py
X-GNOME-Autostart-enabled=true
Terminal=false
```

Replace `YOUR_USER` with your username.

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

## Project layout

```
claude-usage-tab/
├── claude_usage_indicator.py   # main daemon
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
