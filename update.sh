#!/usr/bin/env bash
# Self-update for claude-usage-tab.
#
# Pulls the latest code into the current checkout and re-runs install.sh,
# preserving the user's autostart choice (so install.sh never re-prompts).
# Invoked from the indicator's "Update available" action, or run by hand.
#
# Requires a git checkout — that's how the app is installed today. If the
# tree isn't a checkout, it bails with a clear message rather than guessing.

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_URL="https://github.com/Skycun/claude-usage-tab"

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mxx\033[0m  %s\n' "$*" >&2; exit 1; }

command -v git >/dev/null 2>&1 || die "git not found — install git, or reinstall from $REPO_URL"
[[ -d "$DIR/.git" ]] || die "Not a git checkout ($DIR) — reinstall from $REPO_URL"

say "Fetching latest code…"
git -C "$DIR" fetch --tags --prune

if ! git -C "$DIR" pull --ff-only; then
    die "Can't fast-forward (local changes on the branch?). Fix with: git -C \"$DIR\" status"
fi

# Preserve the current autostart setting so install.sh runs non-interactively.
AS_FILE="$HOME/.config/autostart/claude-usage-indicator.desktop"
if [[ -f "$AS_FILE" ]]; then AS_FLAG="--autostart"; else AS_FLAG="--no-autostart"; fi

say "Reinstalling (autostart: ${AS_FLAG#--})…"
"$DIR/install.sh" "$AS_FLAG"

say "Update complete."
