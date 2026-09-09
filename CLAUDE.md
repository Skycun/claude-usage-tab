# CLAUDE.md

Entry point for Claude Code in this repo. Keep this file **short and
current** — it loads into every turn. Put deep content in [README.md](./README.md)
and link from here.

---

## What this is

Desktop indicator that surfaces Claude Code's `/usage` data, on three
shells: the GNOME top bar (Ubuntu + Fedora), the macOS menu bar, and the
Windows notification area. Polls the undocumented OAuth endpoint
`https://api.anthropic.com/api/oauth/usage` and renders a label (or, on
Windows, an icon) + dropdown + notifications.

User-facing overview, install, and troubleshooting: [README.md](./README.md).

---

## Layout

```
claude_usage_indicator.py    # Linux: Indicator class + main() — the GTK UI
claude_usage_menubar.py      # macOS: rumps menu-bar app (same engine)
claude_usage_tray.py         # Windows: pystray tray app (same engine)
topbar.py                    # top-bar label composition (settings-driven)
formatting.py                # shared pure render helpers (durations, bars, to_float)
sound.py                     # shared 80%-session flourish engine (audio + image, all 3)
attention.py                 # Claude Code hook + flag store: which terminals want you back
attention_hooks.py           # registers those hooks in ~/.claude/settings.json (2nd write surface)
handoff.py                   # running-session probe + when a switch is worth offering
trayicon.py                  # Windows: percentage badge / logo images (Pillow only)
winshell.py                  # Windows: dialogs, file picker, autostart, launching
settings_dialog.py           # GTK settings window
strings.py                   # i18n (EN default/FR) — STRINGS dict + t()
settings.py                  # ~/.config/claude-usage-indicator/settings.json
api.py                       # OAuth token (+ macOS Keychain fallback) + usage fetch + refresh
accounts.py                  # multi-account store + capture + switch + refresh
alerts.py                    # history (~/.cache/.../history.jsonl) + engine
updates.py                   # GitHub releases/latest check (throttled + cached)
costs.py                     # daily API cost via ccusage (opt-in, throttled + cached)
version.py                   # reads the VERSION file (single source of truth)
VERSION                      # the version string (e.g. 1.0.0)
install.sh / update.sh / uninstall.sh          # Linux install/self-update/uninstall
install-macos.sh / update-macos.sh / uninstall-macos.sh  # macOS (venv + LaunchAgent)
install-windows.ps1 / update-windows.ps1 / uninstall-windows.ps1  # Windows (venv + HKCU Run)
icons/                       # default, gray, full PNGs (bundled)
assets/                      # kylian sound/image + any custom-mode defaults (bundled)
docs/macos.md                # macOS build notes + caveats
docs/windows.md              # Windows build notes + caveats
test_claude_usage.sh         # one-shot curl to the OAuth endpoint
```

Three front-ends, one engine: `claude_usage_indicator.py` (GTK),
`claude_usage_menubar.py` (rumps) and `claude_usage_tray.py` (pystray) all
drive the same UI-free modules (`api`, `settings`, `alerts`, `accounts`,
`updates`, `costs`, `topbar`, `strings`, `formatting`, `sound`). Keep all
logic in the shared modules — a fix should land once and benefit every
platform. macOS and Windows use a pip venv (no PyGObject constraint there);
Linux stays system-Python.

No package, no build. Sibling modules importing each other directly —
**not** a package (no ``__init__.py``). Dependencies are shallow:
``strings``, ``settings``, ``api`` and ``version`` are leaves
(``api``/``updates`` need only ``requests``); ``formatting`` depends on
``strings``; ``alerts``, ``sound`` and ``costs`` depend on ``settings``;
``accounts`` depends on ``api`` + ``settings``; ``updates`` depends on
``settings`` + ``version``; ``topbar`` depends on ``settings`` + ``strings``
+ ``formatting``; ``settings_dialog`` depends on ``settings`` + ``topbar`` +
``costs`` + ``updates`` + ``strings`` (and GTK); ``trayicon`` depends on
Pillow alone and ``winshell`` on the stdlib alone (both import cleanly off
Windows, so they stay testable anywhere); each front-end imports the rest.

Every read of a value from the usage payload goes through
``formatting.to_float`` — it is the one place that turns a string, a null, a
``NaN`` or a missing key into a number instead of a ``ValueError`` that would
kill the poll loop (see non-negotiable 3).

Update flow: the daemon checks ``github.com/<repo>/releases/latest`` a few
seconds after launch and every 6 h (``updates.check`` throttles the actual
network hit and caches to ``~/.cache/.../update_check.json``). A newer tag
shows a one-shot notification + a *Mise à jour disponible* menu row +
a footer line in Settings ▸ Maintenance; clicking runs ``update.sh`` in a
terminal (``git pull --ff-only`` → ``install.sh``, autostart preserved) —
``update-macos.sh`` / ``update-windows.ps1`` on the other two.
Gated by ``update_check_enabled``. The check must stay **fail-open** — a
network error degrades to "no update", never a crash or a red warning.

API cost (``costs.py``): three gates before anything is shown —
``cost.enabled``, a platform whose front-end path has actually been
exercised (``MACOS_READY`` is **False**: the macOS row/toggle stay hidden
until the rumps handoff is run on a real Mac), and a runner that exists on
this machine. All three live in ``costs.is_available``; no runner means the
row is **hidden**, not filled with an error — the settings dialog is where
that gets explained, when you tick the box. It shells out to `ccusage
<https://github.com/ryoppippi/ccusage>`_, which prices the local
``~/.claude/projects/**/*.jsonl`` transcripts, and shows today / rolling 7 d
/ current month in a dropdown submenu. **Opt-in** (``cost.enabled``, off by
default) and dropdown-only — never in the top bar. The runner is
auto-detected (``ccusage`` → ``bunx`` → ``npx`` → ``pnpm dlx``, PATH plus
the usual per-user Node dirs, because the daemon starts with a stripped
PATH); ``cost.command`` overrides it. When prepending the runner's own
directory to the child's PATH, use the path **as found** — never
``resolve()`` it: ``npx`` and corepack's ``pnpm`` are symlinks into
``lib/node_modules/…``, and resolving hands the child a directory with no
``node`` in it (exit 127, only under the daemon's stripped PATH — a shell
test won't reproduce it). Every run happens **on a worker thread** — a
scan walks the whole transcript tree and takes seconds — and is throttled to
``cost.refresh_minutes``. Like the update check it must stay **fail-open**:
no runner, a non-zero exit, or bad JSON degrades to the last cached figures,
never a crash. The figures are an estimate of what that traffic would cost
on the API (all CLI agents ccusage detects, not just Claude); it is not a
bill and has nothing to do with the plan limits shown above.

Terminal attention (``attention.py`` + ``attention_hooks.py``): blink the
icon while a Claude Code terminal has finished its turn or is stuck on a
permission prompt. **Opt-in** (``attention.enabled``, off by default) and it
needs hooks registered in ``~/.claude/settings.json`` — see non-negotiable 7
for the rules that write is held to. Four events are hooked: ``Stop`` flags
the session as *done*, ``Notification`` as *waiting*, and
``UserPromptSubmit`` / ``SessionEnd`` clear it. Each writes one small file
under ``~/.cache/…/attention/``, named by a **sanitised** session id, holding
the folder's basename and never the path (same hygiene rule as
``costs.json``). *waiting* outranks *done* everywhere and blinks at half the
period, because a blocked session is the one that actually costs you time. A
flag nobody cleared expires after ``attention.expire_minutes`` — a terminal
killed with ^C never fires ``SessionEnd``. The whole thing is **fail-open**:
no hooks, no directory, or an unreadable flag means "nothing is waiting",
never a crash. The blink lives on each front-end's own timer (worker-thread
deadline on Windows, self-rescheduling ``GLib`` source on GNOME, fixed-beat
``rumps.Timer`` on macOS) and never on the usage poll — a hook fires the
instant a turn ends and two minutes of latency would defeat the point.
One shell caveat worth remembering: **an icon parked in the notification
area's overflow chevron cannot be seen blinking.**

Account handoff (``handoff.py``): **a running ``claude`` session follows the
credentials file.** This was assumed to be false for a long time, including
by ``accounts.switch_to``'s own docstring, and it cost an afternoon to
disprove: a session authenticated as one account reported the *other* one in
``/status`` forty minutes after a swap, and refreshed that other account's
token back into the file. So a switch moves every open terminal at once.
There is no relaunching, no ``--continue``, no second window — an earlier
version of this feature opened one and it was pure noise.

What remains is small. ``running_sessions`` counts live sessions so the
confirmation can say what the switch is about to affect; it is a heads-up,
never a gate, and ``known=False`` (the probe could not run) is worded
differently from zero. A process is a Claude session when its executable is
named ``claude`` (the native installer drops ``claude.exe`` in
``~/.local/bin``) or its command line names the npm CLI entry point, and
never when it is one of ours. ``pick_offer`` holds the policy: at
``LIMIT_UTIL`` (100%) on the five-hour window, offer the stored account with
the most room, and only if it is under ``ROOM_UTIL`` (90%) — an account
already at 92% buys minutes, not an afternoon. The offer surfaces as a
notification plus a row at the top of the menu, once per limit episode.

The follow-the-file behaviour is **undocumented**, exactly like the usage
endpoint. Treat it as observed, not guaranteed: if a future Claude Code pins
its credentials at startup, the switch quietly degrades to "next launch
only", which makes the feature less useful and never harmful.

Windows tray (``claude_usage_tray.py``): the notification area gives you an
icon and a tooltip and nothing else, so the **number is drawn into the
icon** (``trayicon.badge``) — one number only, the highest of the selected
metrics, because that is the one nearest a limit. ``show_claude`` off
switches back to the plain logo. Four constraints are not negotiable there:

* **Hard string caps.** ``szTip`` is ``WCHAR[128]`` and ``szInfoTitle``
  ``WCHAR[64]``; ctypes *raises* on an over-long assignment, which would kill
  the daemon. Everything reaching the shell goes through ``winshell.clamp``,
  and the tooltip adds detail lines only while they fit.
* **Menu callbacks must return immediately.** They run on the Win32
  message-loop thread, so they only ever push a job onto ``TrayApp._jobs``;
  the worker thread (pystray's ``setup`` callback) does the polling, the
  settings writes and the modal dialogs. A confirmation box or an HTTP call
  on the message thread freezes the tray icon itself.
* **Menu rebuilds are serialised.** pystray rebuilds the native menu after
  every click *and* we rebuild it after every poll, both landing in an
  unlocked ``_update_menu`` that destroys and recreates the ``HMENU``. Hence
  the mutex in ``_Icon``.
* **Every file read and write names ``encoding="utf-8"``.** Windows resolves
  the default text encoding to the ANSI code page (``cp1252`` here), so a
  bare ``read_text()`` on a JSON file throws ``UnicodeDecodeError`` the
  moment the file holds a byte that page does not define. ``~/.claude.json``
  is rewritten constantly and does hold such bytes, which made this an
  *intermittent* dead tick rather than an obvious one. ``UnicodeDecodeError``
  is not caught by ``except json.JSONDecodeError``; name it, or catch
  ``ValueError``.

* **Under ``pythonw.exe`` there is no stderr.** ``sys.stderr`` is ``None``
  and ``print(..., file=None)`` silently does nothing, so every diagnostic
  would vanish — autostart always launches that way. ``_setup_logging``
  redirects to ``%LOCALAPPDATA%/claude-usage-indicator/tray.log``.

``winshell`` and ``trayicon`` never touch ``ctypes.windll`` or Windows at
import time, which is what lets the whole front-end be exercised headlessly
on Linux (``PYSTRAY_BACKEND=dummy``). Keep it that way.

User-visible display options (top-bar metrics, prefixes, compact, metric
separator) live in ``settings.py`` (``Settings`` + ``TopbarSettings``) and
are edited via the GTK ``settings_dialog`` — **not** constants in the
script. The dialog writes ``settings.json`` and the daemon applies it live
(``apply_settings_now``).

Runtime files (never committed):
- ``~/.config/claude-usage-indicator/settings.json`` — user config, auto-created on first run
- ``~/.cache/claude-usage-indicator/history.jsonl`` — 72 h of ``(ts, metric, util)`` samples
- ``~/.config/claude-usage-indicator/accounts.json`` — token-free account index (email/plan/label)
- ``~/.config/claude-usage-indicator/accounts/<id>.json`` — per-account credential blob, ``0600``
- ``~/.cache/claude-usage-indicator/costs.json`` — per-day API cost amounts (``(date, amount)`` only)
- ``~/.cache/claude-usage-indicator/attention/<session>.json`` — one flag per waiting terminal (``kind``, ``ts``, folder name)
- ``~/.claude.json.cusi-bak`` — backup written before a switch rewrites ``~/.claude.json``
- ``~/.claude/settings.json.cusi-bak`` — backup written before the attention hooks are merged in
- ``%LOCALAPPDATA%/claude-usage-indicator/tray.log`` — Windows only, stderr when there's no console (capped at 1 MB)

---

## Non-negotiables

1. **System Python only — on Linux.** Shebang is `#!/usr/bin/python3`.
   PyGObject (`gi`) is provided by `python3-gi` (APT) and is absent from
   most virtualenvs. Do not switch to `python3` (unqualified) or add a venv
   *there*. macOS and Windows have no such constraint and deliberately use
   one (`requirements-macos.txt` / `requirements-windows.txt`).

2. **Never log or expose the OAuth token.** It lives in
   `~/.claude/.credentials.json`. Read it, use it in the `Authorization`
   header, and that's it — no prints, no logs, no error messages that
   include it.

3. **The `/api/oauth/usage` endpoint is undocumented.** Treat every
   field as optional. If Anthropic changes the shape, the script must
   degrade gracefully, not crash. Every `.get(...)` call should have a
   sensible default.

4. **429 is not an error.** It's the known Anthropic bug
   ([claude-code #31021](https://github.com/anthropics/claude-code/issues/31021)).
   Handle it via the existing exponential-backoff path — keep stats
   visible, switch icon to gray, retry later. Never surface it as a red
   warning.

5. **Never write user identifiers to ``history.jsonl``.** Only
   ``(ts, metric, util)`` triples. No email, no token, no account id.
   The cache is not encrypted and the user may share it when debugging.
   Same rule for ``costs.json`` — ``(date, amount)`` pairs only, never a
   project path or a session id, even though ccusage knows both.

6. **New user-visible strings go through ``strings.py``.** Add the key in
   **every** language table (EN, FR, ES, DE, JA, PT — all must share the
   same key set). English is the default; a missing key falls back to
   English with a stderr warning — fine for debugging, not OK to ship.
   Keep top-bar-visible strings (``session_5h``/``weekly_7d``/``sonnet_7d``
   and the ``label_*`` segments) ASCII-safe — the GNOME top-bar font drops
   many non-ASCII glyphs. Remember the Windows tooltip is capped at 127
   characters: a long new string costs a detail line there.

7. **The account store holds several OAuth tokens — guard it like #2.**
   ``accounts/<id>.json`` files are the only place besides ``~/.claude``
   that hold tokens; write them ``0600`` and never log/echo them. The
   ``account_switch_enabled`` flag gates one of the *two* pieces of code
   that **write** into ``~/.claude`` (``accounts.switch_to``): it replaces
   just ``claudeAiOauth`` / ``oauthAccount``, backs up ``~/.claude.json``
   first, writes atomically, and only affects the next ``claude`` launch.
   Never commit the store or backup.

   **The second write surface is ``attention_hooks``**, and it is the only
   other one there will be. It touches exactly one key
   (``hooks``) of ``~/.claude/settings.json``, only ever on an explicit
   click, after a confirmation, and after backing the file up to
   ``settings.json.cusi-bak``. The merge is additive (the user's own hooks
   for the same events are preserved) and the uninstall removes only
   entries whose command names our own ``attention.py``. Anything that
   would widen this — writing another key, editing project-level settings,
   registering a hook that isn't ours — is out of bounds. ``handoff`` reads
   ``~/.claude`` and never writes to it; keep it that way.

8. **Every ``accounts.switch_to`` caller runs ``handoff.running_sessions``
   first and puts the answer in front of the user.** A switch is not a local
   act: open sessions follow the credentials file, so it moves every terminal
   the user has running. Say how many, then proceed — the default answer is
   yes. Treat ``known=False`` the same as busy, never as "all clear".

---

## How to test a change

```bash
# syntax check
python3 -c "import ast; ast.parse(open('claude_usage_indicator.py').read())"

# restart daemon
pkill -f claude_usage_indicator.py
setsid /usr/bin/python3 claude_usage_indicator.py \
  > /tmp/claude_usage_indicator.log 2>&1 < /dev/null &
disown

# watch logs
tail -f /tmp/claude_usage_indicator.log
```

There are no unit tests. On Linux the only way to verify is to run the
daemon and look at the GNOME top bar.

The attention flags need no Claude session to exercise — the hook reads a
JSON payload on stdin, so you can fire one by hand and watch the front-end
react:

```bash
echo '{"hook_event_name":"Stop","session_id":"probe","cwd":"/tmp/demo"}' \
  | python3 attention.py hook
python3 attention.py status          # {"waiting": 0, "done": 1, ...}
python3 attention.py clear
python3 attention_hooks.py status    # is it registered, and with which command?
python3 attention_hooks.py selftest  # run the real command through the shell
```

The handoff module is equally inspectable from a shell, and worth checking
on any machine where the switch misbehaves:

```bash
python3 handoff.py probe      # sessions=2 known=True notable=True
```

``selftest`` is the one that matters after touching ``command()``: the hook
only ever runs inside Claude Code's own shell, so a quoting mistake shows up
as "the icon never blinks" with nothing in any log to explain it.

The Windows front-end *can* be exercised without Windows, which is how it
was written — real ``pystray`` on its dummy backend, a fake ``HOME``, a
stubbed ``fetch_usage``:

```bash
pip download --no-deps pystray -d /tmp/dl && unzip -o /tmp/dl/*.whl -d /tmp/ps
HOME=/tmp/winhome PYSTRAY_BACKEND=dummy PYTHONPATH=/tmp/ps:. python3 - <<'EOF'
import claude_usage_tray as T

def walk(menu, depth=0):
    for it in (menu or []):
        if it is T.Menu.SEPARATOR:
            print("  " * depth + "---"); continue
        flags = [n for n, on in (("x", it.checked), ("dim", not it.enabled)) if on]
        print("  " * depth + it.text + (f"   {flags}" if flags else ""))
        walk(it.submenu, depth + 1)

class Probe(T._Icon):          # the dummy backend leaves these abstract
    def _update_icon(self): self._icon_valid = True
    def _update_title(self): pass
    def _update_menu(self): walk(self.menu)   # what win32 does, minus the HMENU

T.read_token = lambda: "x"
T.fetch_usage = lambda _t: {"five_hour": {"utilization": 42}}
app = T.TrayApp()
app.icon = Probe("probe", icon=app.icon.icon, menu=T.Menu(app._menu_items))
app._run_tick()
print("TOOLTIP:", app.icon.title)
EOF
```

``walk`` deliberately touches ``.text``/``.checked``/``.enabled``/
``.submenu`` — the same properties the win32 backend reads while building an
``HMENU``, which is where an API misuse surfaces (pystray rejects a bare bool
for ``checked``, and any action taking more than two arguments). Clicking is
``icon._handler(item)()``; the queued job then runs on the next
``_jobs.get``. The PowerShell installers parse- and lint-check with ``pwsh``'s
``[Parser]::ParseFile`` plus ``PSScriptAnalyzer``; keep them **pure ASCII**,
because Windows PowerShell 5.1 reads a BOM-less ``.ps1`` as ANSI.

---

## Icon resolution

`pick_icon()` at the top of the script picks between three PNGs based on
utilisation:

| State | Icon |
|---|---|
| `f_util >= 100 or s_util >= 100` | `full-claude.png` |
| `f_util == 0` (no session or rate-limited branch sets it manually) | `gray-claude.png` |
| else | `claude.png` |

Icons are looked up in `~/.local/share/claude-usage-indicator/` first
(user override) and fall back to `./icons/` next to the script.

---

## Things not to do

- Don't add features on speculation. Every addition must map to a real
  UX gap (the user will tell you).
- Don't rewrite the endpoint call to use `anthropic` SDK. The SDK
  doesn't cover OAuth usage; the raw `requests.get` is correct.
- Don't add CLI flags. User-facing options go through `settings.json`
  (validated in `settings.py`) and the GTK settings window — not flags.
- Don't use emojis in the top-bar label. The GNOME top-bar font drops
  most of them (we already hit this with 🗓). ASCII text only. This is
  why `topbar` separators are sanitized to printable ASCII.
- Don't commit anything from `~/.claude/`. Ever.

---

## Upstream to watch

If Anthropic ships an official usage API/CLI, migrate to it and delete
the OAuth-endpoint code path:

- [claude-code #44328](https://github.com/anthropics/claude-code/issues/44328) — proposal for `claude usage`.
- [claude-code #23975](https://github.com/anthropics/claude-code/issues/23975) — rate-limit in statusLine JSON.
- [claude-code #32796](https://github.com/anthropics/claude-code/issues/32796) — expose Max plan limits via SDK.
