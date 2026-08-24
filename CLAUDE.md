# CLAUDE.md

Entry point for Claude Code in this repo. Keep this file **short and
current** — it loads into every turn. Put deep content in [README.md](./README.md)
and link from here.

---

## What this is

Single-file GNOME AppIndicator (Ubuntu + Fedora) that surfaces Claude Code's
`/usage` data in the top bar. Polls the undocumented OAuth endpoint
`https://api.anthropic.com/api/oauth/usage` every 60 s and renders a
label + dropdown + notifications.

User-facing overview, install, and troubleshooting: [README.md](./README.md).

---

## Layout

```
claude_usage_indicator.py    # Linux: Indicator class + main() — the GTK UI
claude_usage_menubar.py      # macOS: rumps menu-bar app (same engine)
topbar.py                    # top-bar label composition (settings-driven)
formatting.py                # shared pure render helpers (durations, bars, icon state)
sound.py                     # shared 80%-session flourish engine (audio + image, both platforms)
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
icons/                       # default, gray, full PNGs (bundled)
assets/                      # kylian sound/image + any custom-mode defaults (bundled)
docs/macos.md                # macOS build notes + caveats
test_claude_usage.sh         # one-shot curl to the OAuth endpoint
```

Two front-ends, one engine: `claude_usage_indicator.py` (GTK) and
`claude_usage_menubar.py` (rumps) both drive the same UI-free modules
(`api`, `settings`, `alerts`, `accounts`, `updates`, `costs`, `topbar`,
`strings`, `formatting`, `sound`). Keep all logic in the shared modules — a fix should land once
and benefit both platforms. The macOS build uses a pip venv (no PyGObject
constraint there); Linux stays system-Python.

No package, no venv, no build. Sibling modules importing each other
directly — **not** a package (no ``__init__.py``). Dependencies are
shallow: ``strings``, ``settings``, ``api`` and ``version`` are leaves
(``api``/``updates`` need only ``requests``); ``alerts`` depends on
``settings``; ``sound`` depends on ``settings``; ``costs`` depends on
``settings``; ``accounts`` depends on
``api`` + ``settings``; ``updates``
depends on ``settings`` + ``version``; ``topbar`` depends on ``settings`` +
``strings``; ``settings_dialog`` depends on ``settings`` + ``topbar`` +
``costs`` + ``strings`` (and GTK); the main script imports the rest.

Update flow: the daemon checks ``github.com/<repo>/releases/latest`` a few
seconds after launch and every 6 h (``updates.check`` throttles the actual
network hit and caches to ``~/.cache/.../update_check.json``). A newer tag
shows a one-shot notification + a *Mise à jour disponible* menu row +
a footer line in Settings ▸ Maintenance; clicking runs ``update.sh`` in a
terminal (``git pull --ff-only`` → ``install.sh``, autostart preserved).
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
- ``~/.claude.json.cusi-bak`` — backup written before a switch rewrites ``~/.claude.json``

---

## Non-negotiables

1. **System Python only.** Shebang is `#!/usr/bin/python3`. PyGObject
   (`gi`) is provided by `python3-gi` (APT) and is absent from most
   virtualenvs. Do not switch to `python3` (unqualified) or add a venv.

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
   many non-ASCII glyphs.

7. **The account store holds several OAuth tokens — guard it like #2.**
   ``accounts/<id>.json`` files are the only place besides ``~/.claude``
   that hold tokens; write them ``0600`` and never log/echo them. The
   ``account_switch_enabled`` flag gates the *only* code that **writes**
   into ``~/.claude`` (``accounts.switch_to``): it replaces just
   ``claudeAiOauth`` / ``oauthAccount``, backs up ``~/.claude.json``
   first, writes atomically, and only affects the next ``claude`` launch.
   Never widen that write surface, and never commit the store or backup.

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

There are no unit tests. The only way to verify is to run the daemon
and look at the GNOME top bar.

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
