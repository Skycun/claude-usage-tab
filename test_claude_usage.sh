#!/usr/bin/env bash
# Test the undocumented Claude Code OAuth usage endpoint.
# Reads the OAuth token from ~/.claude/.credentials.json and queries
# https://api.anthropic.com/api/oauth/usage — the same endpoint Claude
# Code's /usage command uses internally.
#
# Run it yourself: ./scripts/test_claude_usage.sh

set -euo pipefail

CREDS="$HOME/.claude/.credentials.json"

if [ ! -f "$CREDS" ]; then
  echo "ERROR: $CREDS not found — are you logged in to Claude Code?" >&2
  exit 1
fi

TOKEN=$(jq -r '.claudeAiOauth.accessToken // .accessToken' "$CREDS")

if [ -z "$TOKEN" ] || [ "$TOKEN" = "null" ]; then
  echo "ERROR: no accessToken found in $CREDS" >&2
  exit 1
fi

echo "Token OK (length ${#TOKEN})"
echo "GET https://api.anthropic.com/api/oauth/usage"
echo "---"

curl -sS -w "\n---HTTP %{http_code}---\n" \
  -H "Authorization: Bearer $TOKEN" \
  -H "anthropic-beta: oauth-2025-04-20" \
  -H "Content-Type: application/json" \
  https://api.anthropic.com/api/oauth/usage \
  | (jq . 2>/dev/null || cat)
