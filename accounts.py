"""Multi-account store + switcher for Claude Code.

Claude Code keeps a single active account in ``~/.claude/.credentials.json``
(the OAuth blob) and ``~/.claude.json`` (the ``oauthAccount`` block). Logging
into another account overwrites both. This module lets the indicator remember
every account it has seen, poll each one's usage, and swap the active one back
in on demand.

Layout (all under the app's own config dir, never in ``~/.claude``):

* ``accounts/<id>.json`` — the full credential blob for one account
  (``{"claudeAiOauth": {...}, "oauthAccount": {...}}``), ``chmod 0600``.
  **This is where the tokens live — never log or echo it.**
* ``accounts.json``      — a token-free index: ``[{id, email, plan, label,
  last_seen}]``. Also ``0600`` (it carries emails).

Security notes
--------------
* The store holds *several* OAuth tokens — a real escalation over the single
  token Claude Code keeps. Every file we write is ``0600`` and no token ever
  reaches a log or an error string.
* :func:`switch_to` is the only place that *writes* into ``~/.claude``. It
  replaces exactly two keys (``claudeAiOauth`` / ``oauthAccount``), backs up
  ``~/.claude.json`` first, and writes atomically. It never commits anything.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from api import (
    CONFIG_PATH,
    CREDS_PATH,
    account_email_plan,
    read_oauth_account,
    read_oauth_credentials,
    refresh_oauth_token,
)
from settings import SETTINGS_DIR

ACCOUNTS_DIR = SETTINGS_DIR / "accounts"
INDEX_PATH = SETTINGS_DIR / "accounts.json"
CONFIG_BACKUP = CONFIG_PATH.parent / (CONFIG_PATH.name + ".cusi-bak")

# Refresh a bit before the real expiry so a poll never races the clock.
_EXPIRY_SKEW_SECONDS = 60


@dataclass(frozen=True)
class Account:
    id: str
    email: str
    plan: str
    label: str
    last_seen: str


# --------------------------------------------------------------- json helpers


def _load_json(path: Path) -> dict | list | None:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        return None


def _mode_of(path: Path, default: int) -> int:
    try:
        return path.stat().st_mode & 0o777
    except OSError:
        return default


def _atomic_write_json(path: Path, data: object, *, mode: int) -> None:
    """Write ``data`` as JSON to ``path`` atomically with restrictive perms.

    The sibling ``.tmp`` file is *created* already at ``mode`` (``0600`` for
    credential blobs) via ``os.open`` — a write-then-``chmod`` sequence would
    leave the token material briefly group/world-readable in between. It is
    then ``os.replace``d into place so a crash mid-write can never leave a
    half-written credentials file. Raises ``OSError`` on failure — callers
    decide whether that's fatal.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep our own config / accounts dir owner-only. Never widen ~/.claude,
    # which Claude Code owns — only harden directories we create.
    if path.parent in (ACCOUNTS_DIR, SETTINGS_DIR):
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
    tmp = path.parent / (path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(data, indent=2))
        os.chmod(tmp, mode)  # enforce mode even if the temp file pre-existed
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.replace(tmp, path)


# ------------------------------------------------------------------- store I/O


def slug(email: str | None) -> str:
    """Stable, filesystem-safe id derived from an email address."""
    cleaned = re.sub(r"[^a-z0-9]+", "-", (email or "").strip().lower()).strip("-")
    return cleaned or "account"


def _account_file(acct_id: str) -> Path:
    return ACCOUNTS_DIR / f"{slug(acct_id)}.json"


def _read_index() -> list[dict]:
    data = _load_json(INDEX_PATH)
    if not isinstance(data, list):
        return []
    return [e for e in data if isinstance(e, dict)]


def _write_index(index: list[dict]) -> None:
    try:
        _atomic_write_json(INDEX_PATH, index, mode=0o600)
    except OSError as e:
        print(f"accounts: cannot write index: {e}", file=sys.stderr)


def _write_blob(acct_id: str, blob: dict) -> None:
    try:
        _atomic_write_json(_account_file(acct_id), blob, mode=0o600)
    except OSError as e:
        print(f"accounts: cannot write blob: {e}", file=sys.stderr)


def load_blob(acct_id: str) -> dict | None:
    """Return the full credential blob for an account, or ``None``."""
    path = _account_file(acct_id)
    if not path.exists():
        return None
    data = _load_json(path)
    return data if isinstance(data, dict) else None


def list_accounts() -> list[Account]:
    """Return every stored account, sorted by email for a stable menu order."""
    out: list[Account] = []
    for e in _read_index():
        acct_id = str(e.get("id") or "")
        if not acct_id:
            continue
        email = str(e.get("email") or "")
        out.append(
            Account(
                id=acct_id,
                email=email,
                plan=str(e.get("plan") or ""),
                label=str(e.get("label") or email or acct_id),
                last_seen=str(e.get("last_seen") or ""),
            )
        )
    out.sort(key=lambda a: a.email.lower())
    return out


def active_id() -> str | None:
    """Return the id of the account currently active in ``~/.claude``."""
    email, _ = account_email_plan()
    return slug(email) if email else None


# ------------------------------------------------------------------- capture


def capture(now: datetime) -> str | None:
    """Snapshot the currently-active account into the store (idempotent).

    Keyed by email. Only rewrites the credential blob when the access token
    actually changed (a relogin or a Claude-Code-side refresh), so steady
    state is zero disk writes. Preserves any user-set ``label``. Returns the
    account id, or ``None`` when there's no usable active account.
    """
    email, plan = account_email_plan()
    if not email:
        return None
    oauth = read_oauth_credentials()
    token = oauth.get("accessToken")
    if not token:
        return None
    acct_id = slug(email)

    existing = load_blob(acct_id) or {}
    existing_token = (existing.get("claudeAiOauth") or {}).get("accessToken")
    token_changed = existing_token != token
    if token_changed:
        _write_blob(
            acct_id,
            {"claudeAiOauth": oauth, "oauthAccount": read_oauth_account()},
        )

    index = _read_index()
    entry = next((e for e in index if e.get("id") == acct_id), None)
    plan_str = plan or ""
    needs_index = (
        token_changed
        or entry is None
        or entry.get("email") != email
        or entry.get("plan") != plan_str
    )
    if needs_index:
        if entry is None:
            entry = {"id": acct_id, "label": email}
            index.append(entry)
        entry["email"] = email
        entry["plan"] = plan_str
        entry["last_seen"] = now.isoformat()
        _write_index(index)
    return acct_id


def forget(acct_id: str) -> None:
    """Remove an account from the store (blob file + index entry)."""
    try:
        _account_file(acct_id).unlink()
    except OSError:
        pass
    _write_index([e for e in _read_index() if e.get("id") != acct_id])


# ------------------------------------------------------------------- refresh


def token_expired(oauth: dict, now: datetime) -> bool:
    """True when the stored access token is at/near its expiry.

    ``expiresAt`` is an epoch in milliseconds (we tolerate seconds too). An
    unknown/invalid value returns ``False`` — we let the actual fetch decide
    rather than refreshing needlessly.
    """
    exp = oauth.get("expiresAt")
    if not isinstance(exp, (int, float)):
        return False
    exp_seconds = exp / 1000 if exp > 1e12 else exp
    return exp_seconds <= now.timestamp() + _EXPIRY_SKEW_SECONDS


def refresh(acct_id: str) -> str | None:
    """Refresh a stored account's token and persist it. Returns the new
    access token, or ``None`` if there's no refresh token or the exchange
    failed (network / Cloudflare / rejected)."""
    blob = load_blob(acct_id)
    if not blob:
        return None
    oauth = blob.get("claudeAiOauth") or {}
    fields = refresh_oauth_token(oauth.get("refreshToken") or "")
    if not fields:
        return None
    merged = {**oauth, **fields}
    blob["claudeAiOauth"] = merged
    _write_blob(acct_id, blob)
    return merged.get("accessToken")


# -------------------------------------------------------------------- switch


def switch_to(acct_id: str, now: datetime) -> bool:
    """Make ``acct_id`` the active account for the *next* ``claude`` launch.

    Snapshots the current active account first (so nothing is lost), then
    replaces ``claudeAiOauth`` in ``~/.claude/.credentials.json`` and
    ``oauthAccount`` in ``~/.claude.json`` — every other key is preserved.
    ``~/.claude.json`` is backed up to ``~/.claude.json.cusi-bak`` first.

    Sessions already running **do** follow this: Claude Code re-reads the
    credentials file while it runs, so a switch moves every open terminal, not
    only the next launch. That was verified by observation and is undocumented
    — see ``handoff`` for the caveat. Returns ``False`` if the target has no
    usable token or a write fails.
    """
    # Never lose the account we're leaving.
    capture(now)

    blob = load_blob(acct_id)
    if not blob:
        return False
    target_oauth = blob.get("claudeAiOauth") or {}
    if not target_oauth.get("accessToken"):
        return False
    target_account = blob.get("oauthAccount") or {}

    try:
        # 1) credentials.json — swap only the OAuth block, keep mcpOAuth etc.
        creds = _load_json(CREDS_PATH)
        creds = creds if isinstance(creds, dict) else {}
        creds["claudeAiOauth"] = target_oauth
        _atomic_write_json(CREDS_PATH, creds, mode=0o600)

        # 2) ~/.claude.json — back up, then swap only oauthAccount.
        cfg = _load_json(CONFIG_PATH)
        cfg = cfg if isinstance(cfg, dict) else {}
        if cfg:
            try:
                _atomic_write_json(
                    CONFIG_BACKUP, cfg, mode=_mode_of(CONFIG_PATH, 0o600)
                )
            except OSError:
                pass  # backup is best-effort — don't block the switch
        cfg["oauthAccount"] = target_account
        _atomic_write_json(CONFIG_PATH, cfg, mode=_mode_of(CONFIG_PATH, 0o600))
    except OSError as e:
        print(f"accounts: switch failed: {e}", file=sys.stderr)
        return False
    return True
