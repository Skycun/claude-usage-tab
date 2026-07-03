# Claude Usage Tab — macOS build

A macOS **menu-bar** version of the indicator, built on
[`rumps`](https://github.com/jaredks/rumps). It reuses the exact same engine
as the Linux app (`api`, `settings`, `alerts`, `accounts`, `updates`,
`topbar`, `strings`, `formatting`) — only the presentation layer differs.

> [!WARNING]
> **Experimental / needs validation on a real Mac.** This build was written
> and cross-checked on Linux, where it can't be run. The engine and all the
> pure logic are shared and tested, but the macOS UI layer (rumps, Keychain,
> Notification Center) hasn't been exercised on hardware yet. Expect a few
> small fixes on first run — please report what you hit.

## Install

```bash
git clone https://github.com/Skycun/claude-usage-tab.git ~/claude-usage-tab
cd ~/claude-usage-tab
./install-macos.sh
```

It creates a local `.venv`, installs `rumps` + `requests` (PyObjC comes
along), registers a **LaunchAgent**, asks whether to start at login, and
launches the app. You should see your usage (e.g. `C 17% . 5%`) in the menu
bar. Click it for the full dropdown.

> Unlike the Linux build (which uses the system Python on purpose), the macOS
> build uses a **virtualenv** — there's no PyGObject constraint here, and
> rumps/PyObjC install cleanly from PyPI.

## Run / stop / logs

```bash
# start now (also done by the installer)
launchctl start com.claude-usage-tab
# stop
launchctl unload ~/Library/LaunchAgents/com.claude-usage-tab.plist   # or Quit in the menu
# logs
tail -f ~/Library/Logs/claude-usage-tab.log
# run in the foreground for debugging
.venv/bin/python3 claude_usage_menubar.py
```

## Updates & uninstall

Same idea as Linux, from the menu or the terminal:

```bash
./update-macos.sh              # git pull + refresh deps + restart
./uninstall-macos.sh           # remove the app, keep your data
./uninstall-macos.sh --purge   # also wipe settings, history, accounts
```

## What's different from the Linux build

| Area | Linux (GTK) | macOS (rumps) |
|---|---|---|
| Shell | AppIndicator top-bar | `NSStatusItem` menu bar |
| Settings | full GTK window + live preview | checkable **Options** submenu + **Edit settings.json…** |
| Notifications | libnotify | Notification Center via `osascript` |
| Autostart | `.desktop` in `~/.config/autostart` | LaunchAgent in `~/Library/LaunchAgents` |
| Credentials | `~/.claude/.credentials.json` | that file **or** the login **Keychain** |
| Terminal actions | `gnome-terminal`/etc. | `Terminal.app` via `osascript` |

Everything else — polling, backoff on 429, multi-account switching, custom
alerts, threshold notifications, update checks — is the shared engine.

## Known caveats to verify on hardware

1. **Keychain service name.** If your Mac stores the Claude Code token in the
   Keychain rather than in `~/.claude/.credentials.json`, `api.read_token`
   tries a few candidate service names (`Claude Code-credentials`,
   `Claude Code`, `claude-code`). If none match, check yours with:
   ```bash
   security find-generic-password -s "Claude Code-credentials" -g 2>&1 | head
   ```
   and add it to `_MACOS_KEYCHAIN_SERVICES` in `api.py`.
2. **Notifications** use `osascript` (no app bundle needed), but macOS may
   ask you to allow notifications for *Script Editor*/*Terminal* the first
   time.
3. **Menu-bar text width.** Long labels (many metrics + labels on) can get
   truncated by macOS — use compact mode or fewer metrics if so.
4. **`rumps` menu rebuild.** The menu is rebuilt each tick; if you notice
   flicker while it's open, that's why.

## Packaging as a proper `.app` (optional, later)

Running via the LaunchAgent + venv is enough day-to-day. For a
double-clickable, self-contained `.app` (nicer notifications, Dock-less
launch), `py2app` is the usual path — not included yet.
