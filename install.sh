#!/usr/bin/env bash
# Multi-distro installer for claude-usage-tab.
#
# Detects Ubuntu/Debian vs Fedora/RHEL via /etc/os-release, installs the
# required system packages, enables the GNOME AppIndicator extension
# (Ubuntu) or prints instructions (Fedora), copies icons, and sets up an
# autostart .desktop file.
#
# Safe to re-run.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ICON_DIR="$HOME/.local/share/claude-usage-indicator"
AUTOSTART_DIR="$HOME/.config/autostart"
APPS_DIR="$HOME/.local/share/applications"
DESKTOP_NAME="claude-usage-indicator.desktop"

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m  %s\n' "$*" >&2; }
die()  { printf '\033[1;31mxx\033[0m  %s\n' "$*" >&2; exit 1; }

# -------------------------------------------------------------------- options
# AUTOSTART: "" until resolved, then "1" (launch at login) or "0" (don't).
# Precedence: --autostart / --no-autostart flag > interactive prompt >
# default (enabled), so `curl | bash` and CI stay non-interactive.
AUTOSTART=""
for arg in "$@"; do
    case "$arg" in
        --autostart)    AUTOSTART=1 ;;
        --no-autostart) AUTOSTART=0 ;;
        -h|--help)
            cat <<EOF
Usage: $0 [--autostart|--no-autostart]

  --autostart     Launch the indicator automatically at every login.
  --no-autostart  Don't launch at login (still added to the app grid, and
                  still started once now).

  With neither flag you'll be asked interactively (default: yes). A
  non-interactive run with no flag defaults to enabling autostart.
EOF
            exit 0
            ;;
        *) die "unknown flag: $arg (use --help)" ;;
    esac
done

# Ask up front (before the sudo/apt output) so all prompts are grouped.
resolve_autostart() {
    if [[ -n "$AUTOSTART" ]]; then
        return  # set by flag
    fi
    if [[ -t 0 ]]; then
        local ans
        read -r -p "$(printf '\033[1;34m==>\033[0m Start Claude Usage Tab automatically at login? [Y/n] ')" ans
        case "${ans,,}" in
            n|no) AUTOSTART=0 ;;
            *)    AUTOSTART=1 ;;
        esac
    else
        AUTOSTART=1
        say "No TTY — defaulting to autostart enabled (use --no-autostart to disable)."
    fi
}
resolve_autostart

# ---------------------------------------------------------------- detect distro
if [[ ! -r /etc/os-release ]]; then
    die "/etc/os-release not found — unsupported system."
fi

# shellcheck source=/dev/null
. /etc/os-release
DISTRO_ID="${ID:-unknown}"
DISTRO_LIKE="${ID_LIKE:-}"

family=""
case "$DISTRO_ID" in
    ubuntu|debian|linuxmint|pop) family="debian" ;;
    fedora|rhel|centos|rocky|almalinux) family="fedora" ;;
    *)
        case " $DISTRO_LIKE " in
            *" debian "*|*" ubuntu "*) family="debian" ;;
            *" fedora "*|*" rhel "*)   family="fedora" ;;
        esac
        ;;
esac

if [[ -z "$family" ]]; then
    warn "Distro '$DISTRO_ID' not recognised — skipping package install."
    warn "Install these manually:"
    warn "  Debian/Ubuntu: gir1.2-ayatanaappindicator3-0.1 python3-gi python3-requests libnotify-bin"
    warn "  Fedora/RHEL:   libayatana-appindicator-gtk3 python3-gobject python3-requests libnotify"
else
    say "Detected distro family: $family ($DISTRO_ID)"
fi

# --------------------------------------------------------------- install pkgs
if [[ "$family" == "debian" ]]; then
    say "Installing packages via apt…"
    sudo apt update
    sudo apt install -y \
        gir1.2-ayatanaappindicator3-0.1 \
        python3-gi \
        python3-requests \
        libnotify-bin
elif [[ "$family" == "fedora" ]]; then
    say "Installing packages via dnf…"
    sudo dnf install -y \
        libayatana-appindicator-gtk3 \
        python3-gobject \
        python3-requests \
        libnotify
fi

# ---------------------------------------------------------- GNOME extension
if command -v gnome-extensions >/dev/null 2>&1; then
    enabled="$(gnome-extensions list --enabled 2>/dev/null || true)"
    if [[ "$family" == "debian" ]]; then
        if ! grep -q "ubuntu-appindicators@ubuntu.com" <<<"$enabled"; then
            say "Enabling ubuntu-appindicators@ubuntu.com"
            gnome-extensions enable ubuntu-appindicators@ubuntu.com || \
                warn "Could not enable extension — install it from https://extensions.gnome.org/extension/615/appindicator-support/"
        fi
    elif [[ "$family" == "fedora" ]]; then
        if ! grep -q "appindicatorsupport@rgcjonas.gmail.com" <<<"$enabled"; then
            warn "GNOME extension 'AppIndicator and KStatusNotifierItem Support' is not enabled."
            warn "Install it from: https://extensions.gnome.org/extension/615/appindicator-support/"
            warn "(Fedora does not ship this extension in its repos.)"
        fi
    fi
else
    warn "gnome-extensions CLI not found — skipping extension check."
fi

# ---------------------------------------------------------------- copy icons
say "Copying icons to $ICON_DIR"
mkdir -p "$ICON_DIR"
cp -u "$REPO_DIR/icons/"*.png "$ICON_DIR/"

# --------------------------------------------------------- .desktop entries
# Render once. The app-grid launcher is always installed; the autostart
# entry (relaunch at login) only when the user opted in.
say "Installing .desktop entries"
mkdir -p "$APPS_DIR"
rendered="$(mktemp)"
trap 'rm -f "$rendered"' EXIT
sed "s|@INSTALL_DIR@|$REPO_DIR|g" "$REPO_DIR/$DESKTOP_NAME" > "$rendered"
install -m 644 "$rendered" "$APPS_DIR/$DESKTOP_NAME"

if (( AUTOSTART )); then
    mkdir -p "$AUTOSTART_DIR"
    install -m 644 "$rendered" "$AUTOSTART_DIR/$DESKTOP_NAME"
    say "Autostart enabled — will relaunch at every login."
else
    # Honour a "no" on re-run by clearing a previously installed entry.
    rm -f "$AUTOSTART_DIR/$DESKTOP_NAME"
    say "Autostart disabled — launch it from the app grid when you want."
fi

# Refresh the desktop database so the app grid picks up the new entry
# immediately (non-fatal if the tool is missing).
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true
fi

# ---------------------------------------------------------------- launch now
# Restart any running instance so the freshly installed code takes effect.
if pgrep -f claude_usage_indicator.py >/dev/null 2>&1; then
    say "Restarting running daemon"
    pkill -f claude_usage_indicator.py || true
    sleep 1
fi

say "Starting the indicator"
setsid /usr/bin/python3 "$REPO_DIR/claude_usage_indicator.py" \
    > /tmp/claude_usage_indicator.log 2>&1 < /dev/null &
disown

# ---------------------------------------------------------------------- done
if (( AUTOSTART )); then
    login_line='It will relaunch automatically on every login.'
else
    login_line='It will NOT start at login — launch it from the app grid, or
re-run ./install.sh --autostart to enable that.'
fi

cat <<EOF

$(say "Install complete — the indicator is running.")

It also appears in your app grid (Super key → "Claude Usage Tab").
$login_line

Tail the logs:    tail -f /tmp/claude_usage_indicator.log
Stop it:          pkill -f claude_usage_indicator.py
EOF
