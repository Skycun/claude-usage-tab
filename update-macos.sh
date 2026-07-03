#!/usr/bin/env bash
# Self-update for the macOS build: pull the latest code, refresh deps, and
# restart the LaunchAgent. Preserves your autostart choice (the plist is
# reloaded, not rewritten). Invoked from the menu's "Update available" action.

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="com.claude-usage-tab"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
VENV="$DIR/.venv"
REPO_URL="https://github.com/Skycun/claude-usage-tab"

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mxx\033[0m  %s\n' "$*" >&2; exit 1; }

command -v git >/dev/null 2>&1 || die "git not found — reinstall from $REPO_URL"
[[ -d "$DIR/.git" ]] || die "Not a git checkout ($DIR) — reinstall from $REPO_URL"

say "Fetching latest code…"
git -C "$DIR" fetch --tags --prune
git -C "$DIR" pull --ff-only || die "Can't fast-forward — fix with: git -C \"$DIR\" status"

if [[ -x "$VENV/bin/pip" ]]; then
    say "Refreshing dependencies…"
    "$VENV/bin/pip" install -q -r "$DIR/requirements-macos.txt"
else
    say "No venv found — running full install…"
    exec "$DIR/install-macos.sh"
fi

say "Restarting the app…"
if [[ -f "$PLIST" ]]; then
    launchctl unload "$PLIST" 2>/dev/null || true
    pkill -f claude_usage_menubar.py 2>/dev/null || true
    launchctl load "$PLIST"
    launchctl start "$LABEL" 2>/dev/null || true
else
    exec "$DIR/install-macos.sh"
fi

say "Update complete."
