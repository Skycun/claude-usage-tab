"""Tests for the i18n helpers in strings.py."""

from __future__ import annotations

import pytest

import strings


@pytest.fixture(autouse=True)
def reset_state():
    strings.set_lang(strings.DEFAULT_LANG)
    strings._missing_keys_warned.clear()
    yield
    strings.set_lang(strings.DEFAULT_LANG)
    strings._missing_keys_warned.clear()


def test_default_lang_is_fr():
    assert strings.current_lang() == "fr"


def test_set_lang_accepts_supported():
    strings.set_lang("en")
    assert strings.current_lang() == "en"


def test_set_lang_rejects_invalid_and_resets_to_default():
    strings.set_lang("en")
    strings.set_lang("xx")
    assert strings.current_lang() == strings.DEFAULT_LANG


def test_detect_lang_env_wins_over_settings(monkeypatch):
    monkeypatch.setenv("CLAUDE_USAGE_LANG", "en")
    assert strings.detect_lang("fr") == "en"


def test_detect_lang_env_rejects_garbage(monkeypatch):
    monkeypatch.setenv("CLAUDE_USAGE_LANG", "klingon")
    # Falls through to settings arg.
    assert strings.detect_lang("en") == "en"


def test_detect_lang_settings_takes_over(monkeypatch):
    monkeypatch.delenv("CLAUDE_USAGE_LANG", raising=False)
    assert strings.detect_lang("en") == "en"
    assert strings.detect_lang("fr") == "fr"


def test_detect_lang_ignores_empty_values(monkeypatch):
    monkeypatch.setenv("CLAUDE_USAGE_LANG", "")
    # Empty env → falls through; empty settings → falls through to system/default.
    lang = strings.detect_lang("")
    assert lang in strings.SUPPORTED_LANGS


def test_t_returns_french_by_default():
    assert strings.t("not_connected") == "non connecté"


def test_t_returns_english_when_switched():
    strings.set_lang("en")
    assert strings.t("not_connected") == "not signed in"


def test_t_formats_named_placeholders():
    assert strings.t("duration_days_hours", d=2, h=3) == "2j 3h"
    assert strings.t("duration_hours_minutes", h=4, m=7) == "4h07"


def test_t_missing_key_returns_key_and_warns_once(capsys):
    out = strings.t("__not_a_real_key__")
    assert out == "__not_a_real_key__"
    err = capsys.readouterr().err
    assert "__not_a_real_key__" in err
    # Second call must not re-warn.
    strings.t("__not_a_real_key__")
    err = capsys.readouterr().err
    assert err == ""


def test_t_en_missing_key_falls_back_to_fr():
    strings.STRINGS["fr"]["__fr_only_key__"] = "fr-value"
    try:
        strings.set_lang("en")
        assert strings.t("__fr_only_key__") == "fr-value"
    finally:
        del strings.STRINGS["fr"]["__fr_only_key__"]


def test_t_bad_placeholders_returns_template_not_raise():
    # Calling without required kwargs must not crash.
    out = strings.t("duration_days_hours")
    assert "{d}" in out or "{h}" in out


def test_all_fr_keys_exist_in_en():
    missing = set(strings.STRINGS["fr"]) - set(strings.STRINGS["en"])
    assert not missing, f"EN is missing keys: {missing}"


def test_no_extra_en_keys_beyond_fr():
    extra = set(strings.STRINGS["en"]) - set(strings.STRINGS["fr"])
    assert not extra, f"EN has stray keys not in FR: {extra}"
