#!/usr/bin/env bash
# Uninstaller for claude-usage-tab.
#
# Reverses what install.sh did at the per-user level:
#   - stops the running daemon
#   - removes the app-grid + autostart .desktop entries
#   - removes the bundled icons from ~/.local/share/claude-usage-indicator
#   - refreshes the desktop database
#   - deletes the log file at /tmp/claude_usage_indicator.log
#
# With --purge, also wipes user data:
#   - ~/.config/claude-usage-indicator/ (settings.json, accounts.json,
#     accounts/<id>.json — the per-account OAuth blobs)
#   - ~/.cache/claude-usage-indicator/ (history.jsonl)
#   - ~/.claude.json.cusi-bak (backup of ~/.claude.json left by an account
#     switch — contains the oauthAccount block)
#
# System packages (python3-gi, python3-requests, …) and the GNOME
# AppIndicator extension are NOT removed — they may be used by other
# apps. Remove them manually if you're sure nothing else depends on them.
#
# Safe to re-run.

set -euo pipefail

PURGE=0
for arg in "$@"; do
    case "$arg" in
        --purge) PURGE=1 ;;
        -h|--help)
            cat <<EOF
Usage: $0 [--purge]

  (default)  Remove the indicator, its autostart entry and its icons,
             but keep your settings and history.

  --purge    Also delete ~/.config/claude-usage-indicator/ (settings.json
             + the stored accounts and their OAuth blobs),
             ~/.cache/claude-usage-indicator/ (history.jsonl), and the
             ~/.claude.json.cusi-bak backup left by an account switch.
EOF
            exit 0
            ;;
        *)
            printf 'unknown flag: %s (use --help)\n' "$arg" >&2
            exit 2
            ;;
    esac
done

ICON_DIR="$HOME/.local/share/claude-usage-indicator"
AUTOSTART_DIR="$HOME/.config/autostart"
APPS_DIR="$HOME/.local/share/applications"
CONFIG_DIR="$HOME/.config/claude-usage-indicator"
CACHE_DIR="$HOME/.cache/claude-usage-indicator"
CLAUDE_BACKUP="$HOME/.claude.json.cusi-bak"
DESKTOP_NAME="claude-usage-indicator.desktop"
LOG_FILE="/tmp/claude_usage_indicator.log"

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m  %s\n' "$*" >&2; }

# ---------------------------------------------------------------- kill daemon
if pgrep -f claude_usage_indicator.py >/dev/null 2>&1; then
    say "Stopping the running daemon"
    pkill -f claude_usage_indicator.py || true
    # Give it a moment; don't escalate to SIGKILL without the user's say-so.
    sleep 1
    if pgrep -f claude_usage_indicator.py >/dev/null 2>&1; then
        warn "Daemon still running — re-run 'pkill -9 -f claude_usage_indicator.py' if it persists."
    fi
fi

# -------------------------------------------------------------- .desktop files
for path in \
    "$AUTOSTART_DIR/$DESKTOP_NAME" \
    "$APPS_DIR/$DESKTOP_NAME"
do
    if [[ -f "$path" ]]; then
        say "Removing $path"
        rm -f "$path"
    fi
done

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true
fi

# ------------------------------------------------------------------- icons
if [[ -d "$ICON_DIR" ]]; then
    say "Removing icon dir $ICON_DIR"
    rm -rf "$ICON_DIR"
fi

# -------------------------------------------------------------------- log
if [[ -f "$LOG_FILE" ]]; then
    rm -f "$LOG_FILE"
fi

# ---------------------------------------------------------------- --purge
if (( PURGE )); then
    for dir in "$CONFIG_DIR" "$CACHE_DIR"; do
        if [[ -d "$dir" ]]; then
            say "Purging $dir"
            rm -rf "$dir"
        fi
    done
    # The account-switch backup lives outside those dirs and holds a full
    # copy of ~/.claude.json (the oauthAccount block) — wipe it too.
    if [[ -f "$CLAUDE_BACKUP" ]]; then
        say "Purging $CLAUDE_BACKUP"
        rm -f "$CLAUDE_BACKUP"
    fi
fi

printf '\n'
say "Uninstall complete."
printf '\n'

if (( ! PURGE )); then
    cat <<EOF
Your settings and history are preserved:
  $CONFIG_DIR
  $CACHE_DIR

Re-run '$0 --purge' if you want to delete them too.

EOF
fi

cat <<EOF
System packages installed by install.sh were NOT removed. If nothing
else on this machine needs them, remove them manually:
  Debian/Ubuntu: sudo apt remove gir1.2-ayatanaappindicator3-0.1 python3-gi python3-requests libnotify-bin
  Fedora/RHEL:   sudo dnf remove libayatana-appindicator-gtk3 python3-gobject python3-requests libnotify

The GNOME AppIndicator extension (if enabled by install.sh) is also
left in place — disable it with 'gnome-extensions disable ...' if
you want.
EOF
