"""Tests for the alert engine and usage history."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import alerts
from settings import AlertDef

UTC = timezone.utc


def make_alert(
    id: str = "a1",
    metric: str = "seven_day",
    delta_pp: float = 10.0,
    window_hours: float = 1.0,
    cooldown_hours: float = 0.5,
    enabled: bool = True,
    label: str = "L",
) -> AlertDef:
    return AlertDef(
        id=id,
        enabled=enabled,
        metric=metric,
        delta_pp=delta_pp,
        window_hours=window_hours,
        cooldown_hours=cooldown_hours,
        label=label,
    )


def _fill_linear(
    history: alerts.History,
    metric: str,
    t0: datetime,
    minutes: int,
    step: float,
    start: float = 0.0,
) -> None:
    for i in range(minutes + 1):
        history.append(metric, start + i * step, t0 + timedelta(minutes=i))


# ------------------------------------------------------------- History


def test_history_append_and_delta_basic():
    h = alerts.History()
    t0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    _fill_linear(h, "seven_day", t0, minutes=60, step=0.1)
    now = t0 + timedelta(minutes=60)
    d = h.delta("seven_day", window_hours=1.0, now=now)
    assert d is not None
    assert 5.9 <= d <= 6.1


def test_history_delta_none_when_no_samples():
    h = alerts.History()
    assert h.delta("seven_day", window_hours=1.0, now=datetime.now(UTC)) is None


def test_history_delta_none_when_too_short():
    h = alerts.History()
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    h.append("seven_day", 5.0, t0)
    h.append("seven_day", 15.0, t0 + timedelta(minutes=5))
    d = h.delta("seven_day", window_hours=2.0, now=t0 + timedelta(minutes=5))
    assert d is None


def test_history_delta_unknown_metric():
    h = alerts.History()
    assert h.delta("not_tracked", 1.0, datetime.now(UTC)) is None


def test_history_evicts_past_retention():
    h = alerts.History()
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    h.append("seven_day", 1.0, t0)
    h.append("seven_day", 2.0, t0 + timedelta(hours=alerts.RETENTION_HOURS + 1))
    samples = h._samples["seven_day"]
    assert len(samples) == 1
    assert samples[0].util == 2.0


def test_history_ignores_unknown_metric_on_append():
    h = alerts.History()
    h.append("martian_day", 42.0, datetime.now(UTC))
    assert all(len(dq) == 0 for dq in h._samples.values())


# ----------------------------------------------------- evaluate_alerts


def test_evaluate_fires_when_delta_exceeded():
    h = alerts.History()
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    _fill_linear(h, "seven_day", t0, minutes=60, step=0.25)  # +15 pp over 1 h
    now = t0 + timedelta(minutes=60)

    alert = make_alert(delta_pp=10, window_hours=1)
    last_fired: dict = {}
    fired = alerts.evaluate_alerts([alert], h, last_fired, set(), now, None)

    assert len(fired) == 1
    assert fired[0].alert_id == "a1"
    assert last_fired["a1"] == now


def test_evaluate_quiet_below_threshold():
    h = alerts.History()
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    _fill_linear(h, "seven_day", t0, minutes=60, step=0.05)  # +3 pp
    now = t0 + timedelta(minutes=60)

    fired = alerts.evaluate_alerts(
        [make_alert(delta_pp=10, window_hours=1)], h, {}, set(), now, None,
    )
    assert fired == []


def test_evaluate_respects_boot_cooldown():
    h = alerts.History()
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    _fill_linear(h, "seven_day", t0, minutes=60, step=0.3)
    now = t0 + timedelta(minutes=60)

    fired = alerts.evaluate_alerts(
        [make_alert(delta_pp=5, window_hours=1)],
        h,
        {},
        set(),
        now,
        boot_cooldown_until=now + timedelta(minutes=1),
    )
    assert fired == []


def test_evaluate_skips_during_cooldown():
    h = alerts.History()
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    _fill_linear(h, "seven_day", t0, minutes=60, step=0.3)
    now = t0 + timedelta(minutes=60)

    alert = make_alert(delta_pp=5, window_hours=1, cooldown_hours=2)
    last_fired = {"a1": now - timedelta(minutes=30)}  # cooldown not expired
    fired = alerts.evaluate_alerts([alert], h, last_fired, set(), now, None)
    assert fired == []


def test_evaluate_rearms_after_metric_reset():
    h = alerts.History()
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    _fill_linear(h, "seven_day", t0, minutes=60, step=0.3)
    now = t0 + timedelta(minutes=60)

    alert = make_alert(delta_pp=5, window_hours=1, cooldown_hours=10)
    last_fired = {"a1": now - timedelta(minutes=10)}
    fired = alerts.evaluate_alerts(
        [alert], h, last_fired, {"seven_day"}, now, None,
    )
    assert len(fired) == 1
    assert last_fired["a1"] == now


def test_evaluate_ignores_disabled_alerts():
    h = alerts.History()
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    _fill_linear(h, "seven_day", t0, minutes=60, step=0.5)
    now = t0 + timedelta(minutes=60)

    fired = alerts.evaluate_alerts(
        [make_alert(delta_pp=5, window_hours=1, enabled=False)],
        h,
        {},
        set(),
        now,
        None,
    )
    assert fired == []


def test_evaluate_fires_when_delta_exactly_equals_threshold():
    h = alerts.History()
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    _fill_linear(h, "seven_day", t0, minutes=60, step=10 / 60)  # exactly +10
    now = t0 + timedelta(minutes=60)

    fired = alerts.evaluate_alerts(
        [make_alert(delta_pp=10, window_hours=1)], h, {}, set(), now, None,
    )
    assert len(fired) == 1


def test_evaluate_multiple_alerts_independent():
    h = alerts.History()
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    _fill_linear(h, "seven_day", t0, minutes=60, step=0.5)  # +30 pp
    _fill_linear(h, "five_hour", t0, minutes=60, step=0.05)  # +3 pp
    now = t0 + timedelta(minutes=60)

    fired = alerts.evaluate_alerts(
        [
            make_alert(id="weekly", metric="seven_day", delta_pp=5, window_hours=1),
            make_alert(id="hourly", metric="five_hour", delta_pp=10, window_hours=1),
        ],
        h,
        {},
        set(),
        now,
        None,
    )
    assert len(fired) == 1
    assert fired[0].alert_id == "weekly"


# -------------------------------------------------- snapshot / load


def test_history_snapshot_and_reload(tmp_path, monkeypatch):
    monkeypatch.setattr(alerts, "HISTORY_PATH", tmp_path / "history.jsonl")
    monkeypatch.setattr(alerts, "CACHE_DIR", tmp_path)

    h = alerts.History()
    now = datetime.now(UTC)
    h.append("seven_day", 42.0, now - timedelta(hours=1))
    h.append("seven_day", 50.0, now - timedelta(minutes=10))
    h.snapshot_to_disk()

    h2 = alerts.History()
    h2.load_from_disk()
    samples = h2._samples["seven_day"]
    assert len(samples) == 2
    assert samples[0].util == 42.0
    assert samples[1].util == 50.0


def test_history_load_filters_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(alerts, "HISTORY_PATH", tmp_path / "history.jsonl")
    monkeypatch.setattr(alerts, "CACHE_DIR", tmp_path)

    old = datetime.now(UTC) - timedelta(hours=alerts.RETENTION_HOURS + 5)
    (tmp_path / "history.jsonl").write_text(
        json.dumps({"ts": old.isoformat(), "metric": "seven_day", "util": 1.0})
        + "\n"
    )

    h = alerts.History()
    h.load_from_disk()
    assert len(h._samples["seven_day"]) == 0


def test_history_load_skips_bad_lines(tmp_path, monkeypatch):
    monkeypatch.setattr(alerts, "HISTORY_PATH", tmp_path / "history.jsonl")
    monkeypatch.setattr(alerts, "CACHE_DIR", tmp_path)

    fresh = datetime.now(UTC) - timedelta(minutes=5)
    content = "\n".join(
        [
            "not json",
            json.dumps({"ts": "not a ts"}),
            json.dumps({"ts": fresh.isoformat(), "metric": "seven_day", "util": 7.0}),
        ]
    )
    (tmp_path / "history.jsonl").write_text(content + "\n")

    h = alerts.History()
    h.load_from_disk()
    samples = h._samples["seven_day"]
    assert len(samples) == 1
    assert samples[0].util == 7.0


def test_history_load_noop_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(alerts, "HISTORY_PATH", tmp_path / "nonexistent.jsonl")
    h = alerts.History()
    h.load_from_disk()  # must not raise
    assert all(len(dq) == 0 for dq in h._samples.values())
