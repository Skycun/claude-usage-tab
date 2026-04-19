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
DESKTOP_NAME="claude-usage-indicator.desktop"

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m  %s\n' "$*" >&2; }
die()  { printf '\033[1;31mxx\033[0m  %s\n' "$*" >&2; exit 1; }

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

# --------------------------------------------------------- autostart .desktop
say "Installing autostart entry at $AUTOSTART_DIR/$DESKTOP_NAME"
mkdir -p "$AUTOSTART_DIR"
sed "s|@INSTALL_DIR@|$REPO_DIR|g" \
    "$REPO_DIR/$DESKTOP_NAME" \
    > "$AUTOSTART_DIR/$DESKTOP_NAME"
chmod 644 "$AUTOSTART_DIR/$DESKTOP_NAME"

# ---------------------------------------------------------------------- done
cat <<EOF

$(say "Install complete.")

Start the daemon now (without logging out first):

    setsid /usr/bin/python3 "$REPO_DIR/claude_usage_indicator.py" \\
        > /tmp/claude_usage_indicator.log 2>&1 < /dev/null &
    disown

Tail the logs:

    tail -f /tmp/claude_usage_indicator.log

Stop it:

    pkill -f claude_usage_indicator.py

Autostart is configured — the indicator will relaunch automatically on
your next login.
EOF
