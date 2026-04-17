"""Claude Code OAuth usage endpoint + credentials reader.

Isolates all HTTP and filesystem reads of ``~/.claude/*`` from the rest of
the codebase. The indicator only cares about :func:`read_token`,
:func:`read_account`, :func:`fetch_usage`, and :func:`parse_iso`.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import requests

from strings import t

CREDS_PATH = Path.home() / ".claude" / ".credentials.json"
CONFIG_PATH = Path.home() / ".claude.json"
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"

_TIER_LABELS = {
    "default_claude_max_20x": "Max 20x",
    "default_claude_max_5x": "Max 5x",
    "default_claude_pro": "Pro",
    "default_claude_free": "Free",
}


def _safe_load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def read_token() -> str | None:
    """Return the OAuth access token from ``~/.claude/.credentials.json``."""
    data = _safe_load_json(CREDS_PATH)
    oauth = data.get("claudeAiOauth") or {}
    return oauth.get("accessToken") or data.get("accessToken")


def read_account() -> str:
    """Return ``'email · Plan'`` for the menu header."""
    cfg = _safe_load_json(CONFIG_PATH)
    account = cfg.get("oauthAccount") or {}
    email = account.get("emailAddress") or account.get("displayName")

    creds = _safe_load_json(CREDS_PATH)
    oauth = creds.get("claudeAiOauth") or {}
    raw = oauth.get("rateLimitTier") or oauth.get("subscriptionType")
    tier = _TIER_LABELS.get(raw, raw) if raw else None

    parts = [p for p in (email, tier) if p]
    return "  ·  ".join(parts) if parts else t("unknown_account")


def fetch_usage(token: str) -> dict:
    """Hit ``/api/oauth/usage`` and return parsed JSON or an error dict.

    429 is signalled via ``{"rate_limited": True}`` — not an error. A
    ``Retry-After`` header is honoured via ``retry_after`` seconds.
    """
    try:
        r = requests.get(
            USAGE_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "anthropic-beta": "oauth-2025-04-20",
                "Content-Type": "application/json",
            },
            timeout=10,
        )
    except requests.RequestException as e:
        return {"error": f"request failed: {type(e).__name__}"}
    if r.status_code == 429:
        out: dict = {"error": "HTTP 429", "rate_limited": True}
        hint = r.headers.get("Retry-After")
        if hint:
            try:
                out["retry_after"] = max(0, int(hint))
            except ValueError:
                pass
        return out
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}"}
    try:
        return r.json()
    except ValueError:
        return {"error": "invalid JSON"}


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
