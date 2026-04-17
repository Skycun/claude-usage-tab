"""Internationalised UI strings.

One big dict keyed by language, then by message id. Missing keys fall back
to French (the project's default). Looked up via :func:`t`.

Language resolution order (highest wins):
  1. ``CLAUDE_USAGE_LANG`` env var
  2. ``lang`` field from settings.json
  3. System locale (``locale.getdefaultlocale()``)
  4. ``DEFAULT_LANG``
"""

from __future__ import annotations

import locale
import os
import sys
from typing import Any

DEFAULT_LANG = "fr"
SUPPORTED_LANGS = ("fr", "en")

# Format placeholders use str.format syntax (``{name}``).
# Keep keys stable — they're referenced from other modules.
STRINGS: dict[str, dict[str, str]] = {
    "fr": {
        # menu items / static
        "not_connected": "non connecté",
        "unknown_account": "Compte inconnu",
        "login_item": "Se connecter à Claude Code",
        "quit": "Quitter",
        "refresh_never": "Rafraîchir  (jamais)",
        "refresh_pending": "Rafraîchir  (…)",
        "refresh_ts": "Rafraîchir  ({ts})",
        "refresh_fail": "Rafraîchir  (échec {ts})",
        "rate_limited": "Rate-limited  (retry dans {d})",
        "open_settings_tip": "Ouvrir {url}",
        "edit_settings": "Éditer les réglages",
        "clear_alerts": "Désactiver les alertes actives",
        # metrics
        "session_5h": "Session 5h",
        "weekly_7d": "Weekly 7j",
        "sonnet_7d": "Sonnet 7j",
        "session_line": "Session 5h  [{bar}] {util:5.1f}%",
        "weekly_line": "Weekly 7j   [{bar}] {util:5.1f}%",
        "sonnet_line": "Sonnet 7j   [{bar}] {util:5.1f}%",
        "sonnet_placeholder": "Sonnet 7j   …",
        "session_placeholder": "Session 5h  …",
        "weekly_placeholder": "Weekly 7j   …",
        "sonnet_none": "Sonnet 7j   –",
        "reset_arrow_placeholder": "    ↳ …",
        "reset_line": "    ↳ reset dans {rem}  ({abs})",
        "error_line": "Erreur : {msg}",
        "extra_placeholder": "Extra       désactivé",
        "extra_disabled": "Extra : désactivé",
        "extra_active": "Extra : {used}/{limit} {currency}",
        # notifications
        "login_title_ok": "Claude Code",
        "login_body_ok": "Termine le login OAuth dans le terminal qui vient de s'ouvrir.",
        "login_title_fail": "Se connecter à Claude Code",
        "login_body_fail": "Aucun terminal trouvé. Ouvre-en un et tape `claude`.",
        "reset_title": "{label} réinitialisée",
        "reset_body": "Compteur revenu à zéro.",
        "threshold_title": "{label} à {util:.0f}%",
        "threshold_body": "Reset {abs} (dans {rem})",
        "settings_reloaded_title": "Réglages rechargés",
        "settings_reloaded_body": "Le fichier de config a été appliqué.",
        "alert_fired_title": "Alerte : {label}",
        "alert_fired_body": "+{delta:.1f} pp sur {window}h ({metric})",
        # durations / format_remaining
        "now": "maintenant",
        "dash": "–",
        "duration_days_hours": "{d}j {h}h",
        "duration_hours_minutes": "{h}h{m:02d}",
        "duration_minutes": "{m}min",
        "duration_seconds": "{s}s",
        # labels used in top-bar
        "label_placeholder": " …",
        "label_guide": " 5h 100%  ·  7j 100% ",
        "label_main": " 5h {f:.0f}%  ·  7j {s:.0f}% ",
        "label_main_alert": " /!\\ 5h {f:.0f}%  ·  7j {s:.0f}% ",
        "label_not_connected": " non connecté ",
        "label_error": " !err ",
        # date format for strftime
        "date_format": "%a %d %b %H:%M",
    },
    "en": {
        "not_connected": "not signed in",
        "unknown_account": "Unknown account",
        "login_item": "Sign in to Claude Code",
        "quit": "Quit",
        "refresh_never": "Refresh  (never)",
        "refresh_pending": "Refresh  (…)",
        "refresh_ts": "Refresh  ({ts})",
        "refresh_fail": "Refresh  (failed {ts})",
        "rate_limited": "Rate-limited  (retry in {d})",
        "open_settings_tip": "Open {url}",
        "edit_settings": "Edit settings",
        "clear_alerts": "Clear active alerts",
        "session_5h": "Session 5h",
        "weekly_7d": "Weekly 7d",
        "sonnet_7d": "Sonnet 7d",
        "session_line": "Session 5h  [{bar}] {util:5.1f}%",
        "weekly_line": "Weekly 7d   [{bar}] {util:5.1f}%",
        "sonnet_line": "Sonnet 7d   [{bar}] {util:5.1f}%",
        "sonnet_placeholder": "Sonnet 7d   …",
        "session_placeholder": "Session 5h  …",
        "weekly_placeholder": "Weekly 7d   …",
        "sonnet_none": "Sonnet 7d   –",
        "reset_arrow_placeholder": "    ↳ …",
        "reset_line": "    ↳ resets in {rem}  ({abs})",
        "error_line": "Error: {msg}",
        "extra_placeholder": "Extra       disabled",
        "extra_disabled": "Extra: disabled",
        "extra_active": "Extra: {used}/{limit} {currency}",
        "login_title_ok": "Claude Code",
        "login_body_ok": "Finish the OAuth login in the terminal that just opened.",
        "login_title_fail": "Sign in to Claude Code",
        "login_body_fail": "No terminal found. Open one and run `claude`.",
        "reset_title": "{label} reset",
        "reset_body": "Counter back to zero.",
        "threshold_title": "{label} at {util:.0f}%",
        "threshold_body": "Resets {abs} (in {rem})",
        "settings_reloaded_title": "Settings reloaded",
        "settings_reloaded_body": "Config file applied.",
        "alert_fired_title": "Alert: {label}",
        "alert_fired_body": "+{delta:.1f} pp over {window}h ({metric})",
        "now": "now",
        "dash": "–",
        "duration_days_hours": "{d}d {h}h",
        "duration_hours_minutes": "{h}h{m:02d}",
        "duration_minutes": "{m}min",
        "duration_seconds": "{s}s",
        "label_placeholder": " …",
        "label_guide": " 5h 100%  ·  7d 100% ",
        "label_main": " 5h {f:.0f}%  ·  7d {s:.0f}% ",
        "label_main_alert": " /!\\ 5h {f:.0f}%  ·  7d {s:.0f}% ",
        "label_not_connected": " not signed in ",
        "label_error": " !err ",
        "date_format": "%a %d %b %H:%M",
    },
}

_current_lang = DEFAULT_LANG
_missing_keys_warned: set[str] = set()


def _system_lang() -> str:
    """Best-effort read of the system UI language from standard env vars.

    ``locale.getdefaultlocale`` is deprecated for removal in Python 3.15,
    so we parse the POSIX locale vars ourselves. The returned string is
    the 2-letter prefix (e.g. ``"fr"`` from ``"fr_FR.UTF-8"``) or ``""``.
    """
    for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        raw = os.environ.get(var, "")
        if raw and raw not in ("C", "POSIX"):
            return raw.split(".")[0].split("_")[0].lower()
    return ""


def detect_lang(settings_lang: str | None = None) -> str:
    """Return the active language. Env > settings > system > default."""
    env = os.environ.get("CLAUDE_USAGE_LANG", "").strip().lower()
    for candidate in (env, (settings_lang or "").strip().lower()):
        if candidate in SUPPORTED_LANGS:
            return candidate
    sys_lang = _system_lang()
    if sys_lang in SUPPORTED_LANGS:
        return sys_lang
    return DEFAULT_LANG


def set_lang(lang: str) -> None:
    """Set the active language globally."""
    global _current_lang
    if lang in SUPPORTED_LANGS:
        _current_lang = lang
    else:
        _current_lang = DEFAULT_LANG


def current_lang() -> str:
    return _current_lang


def t(key: str, **kwargs: Any) -> str:
    """Look up a translated string. Safe on missing keys."""
    table = STRINGS.get(_current_lang) or STRINGS[DEFAULT_LANG]
    template = table.get(key)
    if template is None:
        template = STRINGS[DEFAULT_LANG].get(key)
    if template is None:
        if key not in _missing_keys_warned:
            _missing_keys_warned.add(key)
            print(f"i18n: missing key {key!r}", file=sys.stderr)
        return key
    if not kwargs:
        return template
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        return template


_LOCALE_CANDIDATES = {
    "fr": ("fr_FR.UTF-8", "fr_FR.utf8", "fr_FR", "fr"),
    "en": ("en_US.UTF-8", "en_US.utf8", "en_US", "en_GB.UTF-8", "en"),
}


def setup_locale(lang: str) -> None:
    """Try to set LC_TIME so strftime localises month/day names.

    Silent fallback to the default C locale if the requested locale isn't
    installed — the user just sees English-looking dates.
    """
    for candidate in _LOCALE_CANDIDATES.get(lang, ()):
        try:
            locale.setlocale(locale.LC_TIME, candidate)
            return
        except locale.Error:
            continue
