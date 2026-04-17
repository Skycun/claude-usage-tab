"""In-memory usage history + alert rule engine.

The daemon calls :meth:`History.append` for each metric on every fresh
tick, then :func:`evaluate_alerts` to get the alerts that fired. A small
JSONL file at ``~/.cache/claude-usage-indicator/history.jsonl`` persists
recent samples across restarts — **no token or PII ever ends up here**,
just ``(ts, metric, util)`` triples.

Alert semantics
---------------
An alert fires when, for its metric, the utilisation has grown by at
least ``delta_pp`` points over the last ``window_hours``. The delta is
computed as ``util_now - util_at(now - window_hours)`` using the nearest
sample; if fewer than ``window_hours / 2`` of history is available the
alert is skipped (we'd otherwise falsely fire on cold starts).

Cooldown
--------
After firing, an alert cannot fire again for ``cooldown_hours`` unless a
reset of the metric is detected (caller sets ``reset_ids``). The
``boot_cooldown_until`` parameter guards against all alerts firing at
startup while history is still being built.
"""

from __future__ import annotations

import json
import sys
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from settings import APP_ID, AlertDef, VALID_METRICS

CACHE_DIR = Path.home() / ".cache" / APP_ID
HISTORY_PATH = CACHE_DIR / "history.jsonl"
RETENTION_HOURS = 72
MAX_LINES = 10_000


@dataclass(frozen=True)
class Sample:
    ts: datetime
    metric: str
    util: float


@dataclass(frozen=True)
class AlertFired:
    alert_id: str
    label: str
    metric: str
    delta: float
    window_hours: float


class History:
    """Per-metric deque of samples, plus disk snapshot.

    Samples older than :data:`RETENTION_HOURS` are evicted on every
    :meth:`append`. The disk file is append-only JSONL, truncated when it
    exceeds :data:`MAX_LINES` lines.
    """

    def __init__(self) -> None:
        self._samples: dict[str, deque[Sample]] = {
            m: deque() for m in VALID_METRICS
        }

    def append(self, metric: str, util: float, ts: datetime) -> None:
        if metric not in self._samples:
            return
        dq = self._samples[metric]
        dq.append(Sample(ts=ts, metric=metric, util=float(util)))
        cutoff = ts - timedelta(hours=RETENTION_HOURS)
        while dq and dq[0].ts < cutoff:
            dq.popleft()

    def delta(
        self, metric: str, window_hours: float, now: datetime
    ) -> float | None:
        """Return ``util(now) - util(now - window)`` or ``None`` if unknown.

        ``None`` is returned when (a) the metric has no samples, or (b)
        the oldest sample is younger than ``window_hours / 2`` (not enough
        history to trust the delta).
        """
        dq = self._samples.get(metric)
        if not dq:
            return None
        latest = dq[-1]
        window = timedelta(hours=window_hours)
        oldest_needed = now - window
        min_required = now - window / 2
        if dq[0].ts > min_required:
            return None
        # Pick the sample closest to oldest_needed (first >= oldest_needed
        # or the oldest available).
        reference = dq[0]
        for s in dq:
            if s.ts >= oldest_needed:
                reference = s
                break
        return latest.util - reference.util

    def snapshot_to_disk(self) -> None:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            lines: list[str] = []
            for dq in self._samples.values():
                for s in dq:
                    lines.append(
                        json.dumps(
                            {
                                "ts": s.ts.isoformat(),
                                "metric": s.metric,
                                "util": s.util,
                            }
                        )
                    )
            if len(lines) > MAX_LINES:
                lines = lines[-MAX_LINES:]
            HISTORY_PATH.write_text("\n".join(lines) + ("\n" if lines else ""))
        except OSError as e:
            print(f"history: snapshot failed: {e}", file=sys.stderr)

    def load_from_disk(self) -> None:
        if not HISTORY_PATH.exists():
            return
        try:
            text = HISTORY_PATH.read_text()
        except OSError as e:
            print(f"history: read failed: {e}", file=sys.stderr)
            return
        cutoff = datetime.now(timezone.utc) - timedelta(hours=RETENTION_HOURS)
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                ts = datetime.fromisoformat(obj["ts"])
                metric = obj["metric"]
                util = float(obj["util"])
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts < cutoff or metric not in self._samples:
                continue
            self._samples[metric].append(Sample(ts=ts, metric=metric, util=util))
        # Post-load: ensure samples remain time-ordered per metric.
        for metric, dq in self._samples.items():
            ordered = sorted(dq, key=lambda s: s.ts)
            dq.clear()
            dq.extend(ordered)


def evaluate_alerts(
    alerts: Iterable[AlertDef],
    history: History,
    last_fired: dict[str, datetime],
    reset_metrics: set[str],
    now: datetime,
    boot_cooldown_until: datetime | None,
) -> list[AlertFired]:
    """Return the alerts that should fire *now*.

    ``last_fired`` is mutated in place: when an alert fires, its id is
    keyed to ``now``. When a reset is observed for an alert's metric, the
    entry is removed so the cooldown is dropped.
    """
    # A reset always clears cooldowns on that metric so the user gets
    # re-armed for the next window.
    for alert in alerts:
        if alert.metric in reset_metrics and alert.id in last_fired:
            del last_fired[alert.id]

    if boot_cooldown_until and now < boot_cooldown_until:
        return []

    fired: list[AlertFired] = []
    for alert in alerts:
        if not alert.enabled:
            continue
        cooldown_end = last_fired.get(alert.id)
        if cooldown_end and now < cooldown_end + timedelta(
            hours=alert.cooldown_hours
        ):
            continue
        delta = history.delta(alert.metric, alert.window_hours, now)
        if delta is None:
            continue
        if delta < alert.delta_pp:
            continue
        last_fired[alert.id] = now
        fired.append(
            AlertFired(
                alert_id=alert.id,
                label=alert.label,
                metric=alert.metric,
                delta=delta,
                window_hours=alert.window_hours,
            )
        )
    return fired
