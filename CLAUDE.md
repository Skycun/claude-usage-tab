# CLAUDE.md

Entry point for Claude Code in this repo. Keep this file **short and
current** — it loads into every turn. Put deep content in [README.md](./README.md)
and link from here.

---

## What this is

Single-file Ubuntu/GNOME AppIndicator that surfaces Claude Code's
`/usage` data in the top bar. Polls the undocumented OAuth endpoint
`https://api.anthropic.com/api/oauth/usage` every 60 s and renders a
label + dropdown + notifications.

User-facing overview, install, and troubleshooting: [README.md](./README.md).

---

## Layout

```
claude_usage_indicator.py    # Indicator class + main() — the UI
strings.py                   # i18n (FR/EN) — STRINGS dict + t()
settings.py                  # ~/.config/claude-usage-indicator/settings.json
api.py                       # OAuth token + /api/oauth/usage fetcher
alerts.py                    # history (~/.cache/.../history.jsonl) + engine
icons/                       # default, gray, full PNGs (bundled)
test_claude_usage.sh         # one-shot curl to the OAuth endpoint
```

No package, no venv, no build. Five sibling modules importing each other
directly — **not** a package (no ``__init__.py``). Dependencies are
shallow: ``strings`` is a leaf, ``settings`` and ``api`` depend only on
``strings``, ``alerts`` depends on ``settings``, and the main script
imports all four.

Runtime files (never committed):
- ``~/.config/claude-usage-indicator/settings.json`` — user config, auto-created on first run
- ``~/.cache/claude-usage-indicator/history.jsonl`` — 72 h of ``(ts, metric, util)`` samples

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

6. **New user-visible strings go through ``strings.py``.** Always add
   both FR and EN keys. Missing EN falls back to FR with a stderr
   warning — fine for debugging, not OK to ship.

---

## How to test a change

```bash
# unit tests (pure-logic modules)
python3 -m pytest tests/

# syntax check everything
python3 -m py_compile strings.py settings.py alerts.py api.py \
                      claude_usage_indicator.py

# restart daemon
pkill -f claude_usage_indicator.py
setsid /usr/bin/python3 claude_usage_indicator.py \
  > /tmp/claude_usage_indicator.log 2>&1 < /dev/null &
disown

# watch logs
tail -f /tmp/claude_usage_indicator.log
```

`strings`, `settings`, and `alerts` have unit-test coverage; the GTK UI
in `claude_usage_indicator.py` is verified only by eyeballing the top
bar. **If you change pure-logic code, add or update a test** —
ci.yml will gate PRs on ``pytest``.

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
- Don't add a config file or CLI flags until the user asks — constants
  at the top of the script are fine for now.
- Don't use emojis in the top-bar label. The GNOME top-bar font drops
  most of them (we already hit this with 🗓). ASCII text only.
- Don't commit anything from `~/.claude/`. Ever.

---

## Upstream to watch

If Anthropic ships an official usage API/CLI, migrate to it and delete
the OAuth-endpoint code path:

- [claude-code #44328](https://github.com/anthropics/claude-code/issues/44328) — proposal for `claude usage`.
- [claude-code #23975](https://github.com/anthropics/claude-code/issues/23975) — rate-limit in statusLine JSON.
- [claude-code #32796](https://github.com/anthropics/claude-code/issues/32796) — expose Max plan limits via SDK.
