#!/usr/bin/env bash
# macOS installer for Claude Usage Tab (menu-bar build).
#
# Creates a local virtualenv, installs rumps + requests, and registers a
# LaunchAgent so the app runs (and, if you opt in, relaunches at login).
# Safe to re-run.
#
# Flags: --autostart / --no-autostart skip the interactive prompt.

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="com.claude-usage-tab"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
VENV="$DIR/.venv"
PY="$VENV/bin/python3"
LOG="$HOME/Library/Logs/claude-usage-tab.log"

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mxx\033[0m  %s\n' "$*" >&2; exit 1; }

AUTOSTART=""
for arg in "$@"; do
    case "$arg" in
        --autostart)    AUTOSTART=1 ;;
        --no-autostart) AUTOSTART=0 ;;
        -h|--help)
            cat <<EOF
Usage: $0 [--autostart|--no-autostart]
  --autostart     Relaunch the app automatically at every login.
  --no-autostart  Don't launch at login (still started once now).
  With neither flag you'll be asked (default: yes).
EOF
            exit 0 ;;
        *) die "unknown flag: $arg (use --help)" ;;
    esac
done

[[ "$(uname)" == "Darwin" ]] || die "This installer is for macOS. On Linux use ./install.sh."
command -v python3 >/dev/null 2>&1 || die "python3 not found — install the Xcode Command Line Tools: xcode-select --install"

# --------------------------------------------------------------- ask autostart
if [[ -z "$AUTOSTART" ]]; then
    if [[ -t 0 ]]; then
        read -r -p "$(printf '\033[1;34m==>\033[0m Start Claude Usage Tab automatically at login? [Y/n] ')" ans
        case "${ans,,}" in n|no) AUTOSTART=0 ;; *) AUTOSTART=1 ;; esac
    else
        AUTOSTART=1
        say "No TTY — defaulting to autostart enabled (use --no-autostart to disable)."
    fi
fi

# ------------------------------------------------------------------ venv + deps
say "Setting up virtualenv at $VENV"
python3 -m venv "$VENV"
"$PY" -m pip install --upgrade pip >/dev/null
say "Installing dependencies (rumps, requests, pyobjc)…"
"$VENV/bin/pip" install -r "$DIR/requirements-macos.txt"

# ------------------------------------------------------------- LaunchAgent
mkdir -p "$HOME/Library/LaunchAgents" "$(dirname "$LOG")"
run_at_load="false"; [[ "$AUTOSTART" == "1" ]] && run_at_load="true"

say "Writing LaunchAgent $PLIST (autostart: $run_at_load)"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PY</string>
        <string>$DIR/claude_usage_menubar.py</string>
    </array>
    <key>RunAtLoad</key><$run_at_load/>
    <key>KeepAlive</key><false/>
    <key>StandardOutPath</key><string>$LOG</string>
    <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
PLIST

# ------------------------------------------------------------------ (re)launch
# Stop any previous instance, reload the agent, and start it now regardless of
# the autostart choice (that choice only governs future logins).
launchctl unload "$PLIST" 2>/dev/null || true
pkill -f claude_usage_menubar.py 2>/dev/null || true
launchctl load "$PLIST"
launchctl start "$LABEL" 2>/dev/null || true

cat <<EOF

$(say "Install complete — look for the usage text in your menu bar.")

Logs:   tail -f "$LOG"
Stop:   launchctl unload "$PLIST"   (or use Quit in the menu)
EOF
if [[ "$AUTOSTART" != "1" ]]; then
    echo "Autostart is OFF — re-run ./install-macos.sh --autostart to enable login launch."
fi
