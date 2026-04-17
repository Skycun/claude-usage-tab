"""Tests for settings parsing and validation."""

from __future__ import annotations

import json

import pytest

import settings


def test_from_raw_fills_defaults():
    s = settings._from_raw({})
    assert s.lang == "fr"
    assert s.poll_seconds == 60
    assert s.builtin_thresholds == (80, 95)
    assert s.alerts == ()


def test_from_raw_drops_alert_with_invalid_metric(capsys):
    s = settings._from_raw(
        {
            "alerts": [
                {
                    "id": "x",
                    "metric": "not_a_metric",
                    "delta_pp": 5,
                    "window_hours": 1,
                }
            ]
        }
    )
    assert s.alerts == ()
    assert "not_a_metric" in capsys.readouterr().err


def test_from_raw_accepts_valid_alert():
    s = settings._from_raw(
        {
            "alerts": [
                {
                    "id": "a1",
                    "enabled": True,
                    "metric": "seven_day",
                    "delta_pp": 20,
                    "window_hours": 12,
                    "cooldown_hours": 6,
                    "label": "test",
                }
            ]
        }
    )
    assert len(s.alerts) == 1
    a = s.alerts[0]
    assert a.id == "a1"
    assert a.metric == "seven_day"
    assert a.delta_pp == 20.0
    assert a.window_hours == 12.0
    assert a.cooldown_hours == 6.0
    assert a.label == "test"


def test_from_raw_dedupes_alert_ids(capsys):
    s = settings._from_raw(
        {
            "alerts": [
                {
                    "id": "dup",
                    "metric": "seven_day",
                    "delta_pp": 5,
                    "window_hours": 1,
                },
                {
                    "id": "dup",
                    "metric": "five_hour",
                    "delta_pp": 10,
                    "window_hours": 2,
                },
            ]
        }
    )
    assert len(s.alerts) == 1
    assert s.alerts[0].metric == "seven_day"
    assert "duplicate alert id" in capsys.readouterr().err


@pytest.mark.parametrize("bad_delta", [0, -5, "junk", None])
def test_from_raw_rejects_non_positive_delta(bad_delta):
    s = settings._from_raw(
        {
            "alerts": [
                {
                    "id": "x",
                    "metric": "five_hour",
                    "delta_pp": bad_delta,
                    "window_hours": 1,
                }
            ]
        }
    )
    assert s.alerts == ()


@pytest.mark.parametrize("bad_window", [0, -2, "junk", None])
def test_from_raw_rejects_non_positive_window(bad_window):
    s = settings._from_raw(
        {
            "alerts": [
                {
                    "id": "x",
                    "metric": "five_hour",
                    "delta_pp": 10,
                    "window_hours": bad_window,
                }
            ]
        }
    )
    assert s.alerts == ()


def test_from_raw_defaults_cooldown_to_half_window():
    s = settings._from_raw(
        {
            "alerts": [
                {
                    "id": "x",
                    "metric": "five_hour",
                    "delta_pp": 10,
                    "window_hours": 12,
                }
            ]
        }
    )
    assert s.alerts[0].cooldown_hours == 6.0


def test_from_raw_rejects_too_small_poll_seconds():
    s = settings._from_raw({"poll_seconds": 3})
    assert s.poll_seconds == 60


def test_from_raw_rejects_garbage_poll_seconds():
    s = settings._from_raw({"poll_seconds": "junk"})
    assert s.poll_seconds == 60


def test_from_raw_filters_thresholds():
    s = settings._from_raw({"builtin_thresholds": ["junk", 200, 50, -3, 90]})
    assert s.builtin_thresholds == (50, 90)


def test_from_raw_falls_back_on_empty_thresholds():
    s = settings._from_raw({"builtin_thresholds": []})
    assert s.builtin_thresholds == (80, 95)


def test_from_raw_disabled_alert_still_parsed():
    s = settings._from_raw(
        {
            "alerts": [
                {
                    "id": "x",
                    "enabled": False,
                    "metric": "five_hour",
                    "delta_pp": 5,
                    "window_hours": 1,
                }
            ]
        }
    )
    assert len(s.alerts) == 1
    assert s.alerts[0].enabled is False


def test_from_raw_non_dict_alerts_ignored():
    s = settings._from_raw({"alerts": "not a list"})
    assert s.alerts == ()


def test_from_raw_alert_not_dict_ignored(capsys):
    s = settings._from_raw({"alerts": ["just-a-string", 42]})
    assert s.alerts == ()
    err = capsys.readouterr().err
    assert "must be an object" in err


def test_ensure_settings_file_creates_default(tmp_path, monkeypatch):
    fake = tmp_path / "settings.json"
    monkeypatch.setattr(settings, "SETTINGS_PATH", fake)
    monkeypatch.setattr(settings, "SETTINGS_DIR", tmp_path)

    settings.ensure_settings_file()

    assert fake.exists()
    data = json.loads(fake.read_text())
    assert data["lang"] == "fr"
    assert data["schema_version"] == settings.SCHEMA_VERSION


def test_ensure_settings_file_does_not_overwrite(tmp_path, monkeypatch):
    fake = tmp_path / "settings.json"
    fake.write_text('{"lang": "en"}')
    monkeypatch.setattr(settings, "SETTINGS_PATH", fake)
    monkeypatch.setattr(settings, "SETTINGS_DIR", tmp_path)

    settings.ensure_settings_file()
    assert json.loads(fake.read_text()) == {"lang": "en"}


def test_load_settings_recovers_from_bad_json(tmp_path, monkeypatch, capsys):
    fake = tmp_path / "settings.json"
    fake.write_text("{ not json at all")
    monkeypatch.setattr(settings, "SETTINGS_PATH", fake)
    monkeypatch.setattr(settings, "SETTINGS_DIR", tmp_path)

    s = settings.load_settings()
    assert s.lang == "fr"
    assert "load failed" in capsys.readouterr().err


def test_load_settings_recovers_from_non_dict_root(tmp_path, monkeypatch, capsys):
    fake = tmp_path / "settings.json"
    fake.write_text('["list", "not", "dict"]')
    monkeypatch.setattr(settings, "SETTINGS_PATH", fake)
    monkeypatch.setattr(settings, "SETTINGS_DIR", tmp_path)

    s = settings.load_settings()
    assert s.lang == "fr"
    assert "must be an object" in capsys.readouterr().err
