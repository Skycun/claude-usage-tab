"""Claude Code OAuth usage endpoint + credentials reader.

Isolates all HTTP and filesystem reads of ``~/.claude/*`` from the rest of
the codebase. The indicator only cares about :func:`read_token`,
:func:`read_account`, :func:`fetch_usage`, and :func:`parse_iso`.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

CREDS_PATH = Path.home() / ".claude" / ".credentials.json"
CONFIG_PATH = Path.home() / ".claude.json"
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"

# On macOS, Claude Code may keep the OAuth blob in the login Keychain rather
# than in a plaintext file. These are the service names it's been seen under;
# we try each read-only via ``security`` and never print the result.
_MACOS_KEYCHAIN_SERVICES = (
    "Claude Code-credentials",
    "Claude Code",
    "claude-code",
)

# Claude Code's public OAuth client id + token endpoints. Undocumented, but
# stable and widely used; the token endpoint moved from console.anthropic.com
# to platform.claude.com — we try the new one first, fall back to the old.
OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
TOKEN_URLS = (
    "https://platform.claude.com/v1/oauth/token",
    "https://console.anthropic.com/v1/oauth/token",
)

_TIER_LABELS = {
    "default_claude_max_20x": "Max 20x",
    "default_claude_max_5x": "Max 5x",
    "default_claude_pro": "Pro",
    "default_claude_free": "Free",
}

_SUBSCRIPTION_LABELS = {
    "pro": "Pro",
    "max": "Max",
    "free": "Free",
    "team": "Team",
    "enterprise": "Enterprise",
}


def _safe_load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _keychain_credentials() -> dict:
    """macOS only: read the Claude Code credential blob from the Keychain.

    Runs ``security find-generic-password`` (read-only) for each candidate
    service name and returns the parsed ``claudeAiOauth``-shaped dict, or an
    empty dict on any platform other than macOS / any failure. The stored
    value may be the full JSON blob or a bare token — both are handled. The
    token is never printed or logged.
    """
    if sys.platform != "darwin":
        return {}
    for service in _MACOS_KEYCHAIN_SERVICES:
        try:
            out = subprocess.run(
                ["security", "find-generic-password", "-s", service, "-w"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        secret = (out.stdout or "").strip()
        if out.returncode != 0 or not secret:
            continue
        try:
            data = json.loads(secret)
        except json.JSONDecodeError:
            # A bare token string — wrap it in the expected shape.
            return {"accessToken": secret}
        if isinstance(data, dict):
            return data.get("claudeAiOauth") or data
    return {}


def read_token() -> str | None:
    """Return the OAuth access token.

    Reads ``~/.claude/.credentials.json`` first (Linux, and macOS when Claude
    Code uses a file); on macOS it falls back to the login Keychain.
    """
    data = _safe_load_json(CREDS_PATH)
    oauth = data.get("claudeAiOauth") or {}
    token = oauth.get("accessToken") or data.get("accessToken")
    if token:
        return token
    kc = _keychain_credentials()
    return kc.get("accessToken")


def read_oauth_credentials() -> dict:
    """Return the raw ``claudeAiOauth`` block (tokens included).

    Internal use only — the tokens in here are snapshotted into the account
    store and swapped back on a switch. **Never logged or echoed anywhere.**
    """
    return _safe_load_json(CREDS_PATH).get("claudeAiOauth") or {}


def read_oauth_account() -> dict:
    """Return the raw ``oauthAccount`` block from ``~/.claude.json``."""
    return _safe_load_json(CONFIG_PATH).get("oauthAccount") or {}


def account_email_plan() -> tuple[str | None, str | None]:
    """Return ``(email, plan_label)`` for the currently active account."""
    account = read_oauth_account()
    email = account.get("emailAddress") or account.get("displayName")

    oauth = read_oauth_credentials()
    raw_tier = oauth.get("rateLimitTier")
    raw_sub = oauth.get("subscriptionType")
    # Prefer the specific rateLimitTier label (distinguishes Max 5x vs 20x),
    # but only when it matches a known value — some accounts expose a generic
    # "default_claude_ai" that carries no plan info; fall back to subscriptionType.
    plan = (
        _TIER_LABELS.get(raw_tier)
        or _SUBSCRIPTION_LABELS.get((raw_sub or "").lower())
        or raw_tier
        or raw_sub
    )
    return email, plan


def read_account() -> str:
    """Return ``'Claude | email (Plan)'`` for the menu header."""
    email, plan = account_email_plan()
    return format_provider_header("Claude", email, plan)


def refresh_oauth_token(refresh_token: str) -> dict | None:
    """Exchange a refresh token for a fresh access token.

    Returns an oauth-shaped dict (``accessToken`` / ``refreshToken`` /
    ``expiresAt`` in ms) ready to merge into a stored account, or ``None``
    on any failure (network, non-200, missing fields). The endpoint and
    ``client_id`` are undocumented and a Cloudflare WAF rejects the call on
    some networks — callers must degrade gracefully, never surface a crash.
    **The refresh token is never logged.**
    """
    if not refresh_token:
        return None
    body = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": OAUTH_CLIENT_ID,
    }
    for url in TOKEN_URLS:
        try:
            r = requests.post(url, json=body, timeout=10)
        except requests.RequestException:
            continue
        if r.status_code != 200:
            continue
        try:
            j = r.json()
        except ValueError:
            continue
        access = j.get("access_token")
        if not access:
            continue
        out: dict = {"accessToken": access}
        new_refresh = j.get("refresh_token")
        if new_refresh:
            out["refreshToken"] = new_refresh
        expires_in = j.get("expires_in")
        if isinstance(expires_in, (int, float)) and expires_in > 0:
            out["expiresAt"] = int((time.time() + expires_in) * 1000)
        return out
    return None


def format_provider_header(
    provider: str, email: str | None, plan: str | None
) -> str:
    """Compose ``'Provider | email (Plan)'`` with graceful fallbacks."""
    suffix_parts: list[str] = []
    if email:
        suffix_parts.append(email)
    if plan:
        suffix_parts.append(f"({plan})")
    suffix = " ".join(suffix_parts)
    if suffix:
        return f"{provider} | {suffix}"
    return provider


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
