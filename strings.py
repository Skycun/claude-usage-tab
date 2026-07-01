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
        "edit_settings": "Réglages…",
        "clear_alerts": "Désactiver les alertes actives",
        # settings dialog
        "dlg_title": "Réglages — Claude Usage",
        "dlg_tab_general": "Général",
        "dlg_tab_topbar": "Top-bar",
        "dlg_tab_codex": "Codex",
        "dlg_lang": "Langue",
        "dlg_lang_fr": "Français",
        "dlg_lang_en": "English",
        "dlg_poll": "Rafraîchissement (s)",
        "dlg_thresholds": "Seuils d'alerte (%)",
        "dlg_thresholds_hint": "Séparés par des virgules, ex. 80, 95",
        "dlg_show_claude": "Afficher Claude dans la top-bar",
        "dlg_show_codex": "Afficher Codex dans la top-bar",
        "dlg_metric_five_hour": "Session 5h",
        "dlg_metric_seven_day": "Hebdo 7j",
        "dlg_metric_seven_day_sonnet": "Sonnet 7j",
        "dlg_metric_codex_primary": "Fenêtre principale",
        "dlg_metric_codex_secondary": "Fenêtre secondaire",
        "dlg_order": "Ordre des providers",
        "dlg_order_claude_first": "Claude puis Codex",
        "dlg_order_codex_first": "Codex puis Claude",
        "dlg_provider_prefix": "Afficher les préfixes (C / X)",
        "dlg_alert_prefix": "Afficher le préfixe d'alerte (/!\\)",
        "dlg_metric_labels": "Étiqueter chaque métrique (5h, 7j)",
        "dlg_compact": "Mode compact (une valeur par provider)",
        "dlg_separator": "Séparateur entre providers",
        "dlg_metric_separator": "Séparateur entre métriques",
        "dlg_percent_decimals": "Décimales du pourcentage",
        "dlg_codex_enabled": "Activer Codex (polling + menu)",
        "dlg_codex_enabled_hint": "Désactivé : aucun appel réseau Codex, section masquée partout.",
        "dlg_tab_accounts": "Comptes",
        "dlg_accounts_enabled": "Gérer plusieurs comptes Claude",
        "dlg_accounts_enabled_hint": "Mémorise chaque compte quand tu te connectes et affiche leur usage dans le menu.",
        "dlg_account_switch_enabled": "Autoriser le changement de compte (écrit ~/.claude)",
        "dlg_account_switch_hint": "Réécrit les identifiants de Claude Code. Effet au prochain lancement de `claude` ; peut perturber une session en cours.",
        "dlg_preview": "Aperçu :",
        "dlg_preview_icon_only": "(icône seule)",
        "dlg_save": "Enregistrer",
        "dlg_cancel": "Annuler",
        "dlg_edit_json": "Éditer le JSON (alertes…)",
        "dlg_save_failed_title": "Échec de l'enregistrement",
        # metrics
        "session_5h": "5h",
        "weekly_7d": "7j",
        "sonnet_7d": "S7",
        "session_line": "  5h  [{bar}] {util:3.0f}%  ↳ {rem}",
        "weekly_line": "  7j  [{bar}] {util:3.0f}%  ↳ {rem}",
        "sonnet_line": "  S7  [{bar}] {util:3.0f}%",
        "session_placeholder": "  5h  …",
        "weekly_placeholder": "  7j  …",
        "sonnet_placeholder": "  S7  …",
        "error_line": "Erreur : {msg}",
        "extra_active": "  $$  {used}/{limit} {currency}",
        "codex_line": "  {win:<2}  [{bar}] {util:3.0f}%  ↳ {rem}",
        "codex_placeholder": "  {win:<2}  …",
        "codex_credits_balance": "  $$  {balance} {currency}",
        "codex_login_expired": "Codex | login expiré — relance `codex`",
        "codex_account_placeholder": "Codex | …",
        "codex_window_5h": "5h",
        "codex_window_7d": "7j",
        "codex_window_1h": "1h",
        "codex_window_1d": "1j",
        "codex_window_30d": "30j",
        "codex_window_other": "w?",
        "codex_metric_label": "Codex {win}",
        # multi-account (dropdown submenu)
        "accounts_menu": "Comptes Claude",
        "acct_active_mark": "●",
        "acct_inactive_mark": "○",
        "acct_usage": "5h {five:.0f}% · 7j {seven:.0f}%",
        "acct_usage_expired": "token expiré — bascule pour rafraîchir",
        "acct_usage_rl": "rate-limited",
        "acct_usage_error": "indisponible",
        "acct_switch": "Basculer sur ce compte",
        "acct_switch_active": "Compte actif",
        "acct_switch_disabled": "Changement désactivé (voir Réglages)",
        "acct_forget": "Oublier ce compte",
        "acct_switch_confirm_title": "Basculer de compte Claude ?",
        "acct_switch_confirm_body": "Claude Code utilisera {email} au prochain lancement de `claude`. Une session déjà ouverte ne changera pas à chaud.",
        "acct_switch_ok_title": "Compte Claude changé",
        "acct_switch_ok_body": "{email} sera utilisé au prochain lancement de `claude`.",
        "acct_switch_fail_title": "Échec du changement de compte",
        "acct_switch_fail_body": "Impossible d'écrire les identifiants (permissions de ~/.claude ?).",
        "acct_forget_confirm_title": "Oublier ce compte ?",
        "acct_forget_confirm_body": "{email} sera retiré de la liste et ses identifiants stockés supprimés (aucun effet sur ~/.claude).",
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
        # labels used in top-bar (composed in code by topbar.py)
        # ``label_guide_both`` is the width-reservation guide string; the
        # status segments below are the non-"ok" states (the ok segment is
        # built dynamically from the selected metrics).
        "label_placeholder": " …",
        "label_guide_both": " /!\\ C 99% . 99% | X 99% . 99% ",
        "label_seg_claude_rl": "C !RL",
        "label_seg_claude_err": "C !ERR",
        "label_seg_codex_rl": "X !RL",
        "label_seg_codex_login": "X !LOGIN",
        "label_seg_codex_err": "X !ERR",
        "label_alert_prefix": "/!\\",
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
        "edit_settings": "Settings…",
        "clear_alerts": "Clear active alerts",
        # settings dialog
        "dlg_title": "Settings — Claude Usage",
        "dlg_tab_general": "General",
        "dlg_tab_topbar": "Top-bar",
        "dlg_tab_codex": "Codex",
        "dlg_lang": "Language",
        "dlg_lang_fr": "Français",
        "dlg_lang_en": "English",
        "dlg_poll": "Refresh (s)",
        "dlg_thresholds": "Alert thresholds (%)",
        "dlg_thresholds_hint": "Comma-separated, e.g. 80, 95",
        "dlg_show_claude": "Show Claude in the top-bar",
        "dlg_show_codex": "Show Codex in the top-bar",
        "dlg_metric_five_hour": "5h session",
        "dlg_metric_seven_day": "7-day weekly",
        "dlg_metric_seven_day_sonnet": "Sonnet 7-day",
        "dlg_metric_codex_primary": "Primary window",
        "dlg_metric_codex_secondary": "Secondary window",
        "dlg_order": "Provider order",
        "dlg_order_claude_first": "Claude then Codex",
        "dlg_order_codex_first": "Codex then Claude",
        "dlg_provider_prefix": "Show prefixes (C / X)",
        "dlg_alert_prefix": "Show alert prefix (/!\\)",
        "dlg_metric_labels": "Label each metric (5h, 7d)",
        "dlg_compact": "Compact mode (one value per provider)",
        "dlg_separator": "Separator between providers",
        "dlg_metric_separator": "Separator between metrics",
        "dlg_percent_decimals": "Percent decimals",
        "dlg_codex_enabled": "Enable Codex (polling + menu)",
        "dlg_codex_enabled_hint": "Disabled: no Codex network calls, section hidden everywhere.",
        "dlg_tab_accounts": "Accounts",
        "dlg_accounts_enabled": "Manage multiple Claude accounts",
        "dlg_accounts_enabled_hint": "Remembers each account when you sign in and shows their usage in the menu.",
        "dlg_account_switch_enabled": "Allow switching accounts (writes ~/.claude)",
        "dlg_account_switch_hint": "Rewrites Claude Code credentials. Takes effect on the next `claude` launch; may disrupt a running session.",
        "dlg_preview": "Preview:",
        "dlg_preview_icon_only": "(icon only)",
        "dlg_save": "Save",
        "dlg_cancel": "Cancel",
        "dlg_edit_json": "Edit JSON (alerts…)",
        "dlg_save_failed_title": "Could not save settings",
        "session_5h": "5h",
        "weekly_7d": "7d",
        "sonnet_7d": "S7",
        "session_line": "  5h  [{bar}] {util:3.0f}%  ↳ {rem}",
        "weekly_line": "  7d  [{bar}] {util:3.0f}%  ↳ {rem}",
        "sonnet_line": "  S7  [{bar}] {util:3.0f}%",
        "session_placeholder": "  5h  …",
        "weekly_placeholder": "  7d  …",
        "sonnet_placeholder": "  S7  …",
        "error_line": "Error: {msg}",
        "extra_active": "  $$  {used}/{limit} {currency}",
        "codex_line": "  {win:<2}  [{bar}] {util:3.0f}%  ↳ {rem}",
        "codex_placeholder": "  {win:<2}  …",
        "codex_credits_balance": "  $$  {balance} {currency}",
        "codex_login_expired": "Codex | login expired — run `codex`",
        "codex_account_placeholder": "Codex | …",
        "codex_window_5h": "5h",
        "codex_window_7d": "7d",
        "codex_window_1h": "1h",
        "codex_window_1d": "1d",
        "codex_window_30d": "30d",
        "codex_window_other": "w?",
        "codex_metric_label": "Codex {win}",
        # multi-account (dropdown submenu)
        "accounts_menu": "Claude accounts",
        "acct_active_mark": "●",
        "acct_inactive_mark": "○",
        "acct_usage": "5h {five:.0f}% · 7d {seven:.0f}%",
        "acct_usage_expired": "token expired — switch to refresh",
        "acct_usage_rl": "rate-limited",
        "acct_usage_error": "unavailable",
        "acct_switch": "Switch to this account",
        "acct_switch_active": "Active account",
        "acct_switch_disabled": "Switching disabled (see Settings)",
        "acct_forget": "Forget this account",
        "acct_switch_confirm_title": "Switch Claude account?",
        "acct_switch_confirm_body": "Claude Code will use {email} on the next `claude` launch. A running session won't switch live.",
        "acct_switch_ok_title": "Claude account switched",
        "acct_switch_ok_body": "{email} will be used on the next `claude` launch.",
        "acct_switch_fail_title": "Account switch failed",
        "acct_switch_fail_body": "Could not write credentials (~/.claude permissions?).",
        "acct_forget_confirm_title": "Forget this account?",
        "acct_forget_confirm_body": "{email} will be removed from the list and its stored credentials deleted (no effect on ~/.claude).",
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
        "label_guide_both": " /!\\ C 99% . 99% | X 99% . 99% ",
        "label_seg_claude_rl": "C !RL",
        "label_seg_claude_err": "C !ERR",
        "label_seg_codex_rl": "X !RL",
        "label_seg_codex_login": "X !LOGIN",
        "label_seg_codex_err": "X !ERR",
        "label_alert_prefix": "/!\\",
        "label_not_connected": " not signed in ",
        "label_error": " !err ",
        "date_format": "%a %d %b %H:%M",
    },
}

_current_lang = DEFAULT_LANG
_missing_keys_warned: set[str] = set()


def detect_lang(settings_lang: str | None = None) -> str:
    """Return the active language. Env > settings > system > default."""
    env = os.environ.get("CLAUDE_USAGE_LANG", "").strip().lower()
    for candidate in (env, (settings_lang or "").strip().lower()):
        if candidate in SUPPORTED_LANGS:
            return candidate
    try:
        sys_lang = (locale.getdefaultlocale()[0] or "").split("_")[0].lower()
    except (ValueError, locale.Error):
        sys_lang = ""
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
