# Claude Usage Tab — Windows build

A Windows **system-tray** version of the indicator (the notification area at
the bottom-right of the taskbar), built on
[`pystray`](https://github.com/moses-palmer/pystray) + [Pillow]. It reuses the
exact same engine as the Linux and macOS apps — `api`, `settings`, `alerts`,
`accounts`, `updates`, `costs`, `topbar`, `strings`, `formatting`, `sound` —
so only the presentation layer is new.

> [!WARNING]
> **New build — needs field testing on a real Windows machine.** It was
> written and cross-checked on Linux, where the whole UI layer was exercised
> headlessly against the real `pystray` API (menu construction, every menu
> click, the worker loop, the icon renderer) but never against
> `Shell_NotifyIcon` itself. Expect a few small fixes on first run — please
> report what you hit.

## Install

```powershell
git clone https://github.com/Skycun/claude-usage-tab.git $HOME\claude-usage-tab
cd $HOME\claude-usage-tab
.\install-windows.ps1
```

It creates a local `.venv`, installs `pystray` + `Pillow` + `requests`, asks
whether to start at sign-in, and launches the app. Requires **Python 3.9+**
(get it from [python.org](https://www.python.org/downloads/) and tick *Add
python.exe to PATH*) and **Claude Code** signed in at least once.

If PowerShell refuses to run the script, it's the execution policy:

```powershell
powershell -ExecutionPolicy Bypass -File .\install-windows.ps1
```

> Unlike the Linux build (which uses the system Python on purpose, because
> PyGObject comes from the distro), the Windows build uses a **virtualenv** —
> there is no such constraint here and everything installs cleanly from PyPI.

> [!TIP]
> Windows hides new tray icons behind the `^` chevron. Click it and drag the
> Claude icon onto the taskbar to keep it visible.

## What you actually see

The notification area has no text label — just an icon and a tooltip. So:

| Surface | What it carries |
|---|---|
| **Icon** | The percentage, drawn into a coloured tile: Claude terracotta normally, amber from 80%, red from 95%, grey when the number isn't fresh (rate-limited, error, signed out). |
| **Tooltip** (hover) | The composed label plus as many detail lines as fit in the 127 characters Windows allows. |
| **Menu** (right-click) | The full dropdown: metrics, accounts, API cost, updates, Options, Quit. |
| **Left-click** | Refresh now. |

Only one number fits in a 16-pixel square, so the icon shows the **highest**
of the metrics you selected — the one nearest a limit. The rest are in the
tooltip and the menu. Turning off *Options ▸ Show Claude in the top-bar*
switches the icon back to the plain Claude logo.

## Run / stop / logs

```powershell
# start by hand (no console window)
.\.venv\Scripts\pythonw.exe claude_usage_tray.py

# start with a console, to watch it live
.\.venv\Scripts\python.exe claude_usage_tray.py

# stop
#   right-click the tray icon > Quit

# logs (written only when there is no console, i.e. autostart)
Get-Content -Wait "$env:LOCALAPPDATA\claude-usage-indicator\tray.log"
```

`pythonw.exe` has no console, which means `sys.stderr` is `None` and every
diagnostic would silently evaporate. The app detects that and redirects its
own output to `tray.log` (capped at 1 MB, replaced when it grows past that).

## Autostart

Registered as an **HKCU** run entry — per-user, no elevation, no effect on
anyone else who uses the PC:

```
HKCU\Software\Microsoft\Windows\CurrentVersion\Run\ClaudeUsageTab
```

Toggle it any time from **Options ▸ Start with Windows**, or re-run the
installer with `-Autostart` / `-NoAutostart`.

## Updates & uninstall

Same idea as the other platforms, from the menu or the terminal:

```powershell
.\update-windows.ps1              # git pull + refresh deps + restart
.\uninstall-windows.ps1           # remove the app, keep your data
.\uninstall-windows.ps1 -Purge    # also wipe settings, history, accounts
```

Nothing under `~\.claude` (Claude Code's own credentials) is ever touched.

## What's different from the other builds

| Area | Linux (GTK) | macOS (rumps) | Windows (pystray) |
|---|---|---|---|
| Shell | AppIndicator top bar | `NSStatusItem` menu bar | `Shell_NotifyIcon` tray |
| The number | text label | text label | drawn **into the icon** |
| Settings | full GTK window + live preview | checkable **Options** submenu | checkable **Options** submenu |
| Notifications | libnotify | Notification Center via `osascript` | tray balloon / toast |
| Dialogs | GTK | `rumps.alert` | `MessageBoxW` (ctypes) |
| File picker | GTK | `osascript` | `GetOpenFileNameW` (ctypes) |
| Autostart | `.desktop` in `~/.config/autostart` | LaunchAgent | `HKCU\...\Run` |
| Sounds | ffplay/mpv/paplay… | `afplay` | `winsound` (WAV) / hidden PowerShell `MediaPlayer` (mp3) |
| Python | system Python (no venv) | venv | venv |

Everything else — polling, backoff on 429, multi-account switching, custom
alerts, threshold notifications, the API cost readout, update checks — is the
shared engine.

Settings live in the same place as on the other platforms,
`%USERPROFILE%\.config\claude-usage-indicator\settings.json`, deliberately:
one layout across all three, matching where Claude Code itself keeps
`~\.claude`. Edit it from **Settings…** in the menu (it opens in your default
editor) — there is no GTK window here.

## Known caveats to verify on hardware

1. **Tooltip length.** Windows caps `szTip` at 127 characters and the
   notification title at 63. Both are clamped before they reach the API
   (ctypes raises on an over-long assignment, which would take the daemon
   down), and detail lines are dropped whole rather than truncated
   mid-sentence. Long account names cost you the last line.
2. **Icon legibility at 100%.** Three digits in a 16-pixel tile is tight.
   The colour carries the alarm; the number is a detail.
3. **Toasts.** They go through the tray icon's balloon mechanism. If Focus
   Assist / Do Not Disturb is on, Windows may suppress them silently.
4. **`ccusage` runners.** Auto-detection looks on `PATH` (with `PATHEXT`, so
   `npx` finds `npx.cmd`) plus `%APPDATA%\npm`, `%LOCALAPPDATA%\pnpm`,
   `%ProgramFiles%\nodejs`, `~\.bun\bin` and Volta. If yours lives elsewhere,
   set `cost.command` in `settings.json`.
5. **Explorer restarts.** `pystray` re-adds the icon on `WM_TASKBARCREATED`,
   so it should survive one. Worth confirming.

## Packaging as an .exe (optional, later)

Running from the venv is enough day-to-day. For a double-clickable,
self-contained build, PyInstaller (`--noconsole --onefile`) is the usual
path — not included yet.

[Pillow]: https://python-pillow.org/
