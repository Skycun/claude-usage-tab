#!/usr/bin/env bash
# Uninstaller for the macOS build.
#
#   (default)  Unload the LaunchAgent, stop the app, remove the agent + venv.
#   --purge    Also delete your settings, history, stored accounts and the
#              ~/.claude.json.cusi-bak backup.
#
# System-wide Python and Claude Code itself are never touched.

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="com.claude-usage-tab"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
VENV="$DIR/.venv"
LOG="$HOME/Library/Logs/claude-usage-tab.log"
CONFIG_DIR="$HOME/.config/claude-usage-indicator"
CACHE_DIR="$HOME/.cache/claude-usage-indicator"
CLAUDE_BACKUP="$HOME/.claude.json.cusi-bak"

PURGE=0
for arg in "$@"; do
    case "$arg" in
        --purge) PURGE=1 ;;
        -h|--help) echo "Usage: $0 [--purge]"; exit 0 ;;
        *) printf 'unknown flag: %s (use --help)\n' "$arg" >&2; exit 2 ;;
    esac
done

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }

say "Unloading LaunchAgent"
launchctl unload "$PLIST" 2>/dev/null || true
pkill -f claude_usage_menubar.py 2>/dev/null || true
rm -f "$PLIST" "$LOG"

if [[ -d "$VENV" ]]; then
    say "Removing virtualenv $VENV"
    rm -rf "$VENV"
fi

if (( PURGE )); then
    for target in "$CONFIG_DIR" "$CACHE_DIR"; do
        [[ -d "$target" ]] && { say "Purging $target"; rm -rf "$target"; }
    done
    [[ -f "$CLAUDE_BACKUP" ]] && { say "Purging $CLAUDE_BACKUP"; rm -f "$CLAUDE_BACKUP"; }
fi

say "Uninstall complete."
if (( ! PURGE )); then
    echo "Your settings and history are kept. Re-run '$0 --purge' to wipe them."
fi
