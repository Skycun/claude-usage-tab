#!/usr/bin/env python3
"""Put the machine to sleep once Claude is done, or after a set delay.

The use case is a long job left running: start it, arm this, walk away, and
the machine goes to sleep on its own instead of idling all night. Two ways to
arm it, both one-shot:

* :func:`after_finish` — sleep once **every** running Claude Code turn has
  ended. Fed by ``attention.turns``, so it needs the attention hooks
  registered (not the blink itself). "Every" rather than "the next one" on
  purpose: with two jobs running, the short one must not cut the long one
  off mid-flight.
* :func:`after_delay` — sleep in N minutes (:data:`DELAY_PRESETS`), whatever
  the terminals are doing.

Design notes worth keeping:

* **Pure state machine, no clock, no I/O.** :func:`advance` takes the time
  and whether anything is running, and returns the next :class:`Plan` plus
  one event for the front-end to act on. The front-ends own the beat, the
  toasts and the menu; the policy is written once.
* **Never straight to sleep.** Every trigger first opens a
  :data:`GRACE_SECONDS` countdown the user can cancel from the menu. In
  "finished" mode a terminal that starts working again during the countdown
  calls it off, and the plan goes back to waiting.
* **One-shot, and disarmed before sleeping.** :func:`advance` returns no plan
  with :data:`EVENT_SLEEP`, so a machine woken up again stays awake.
* **A machine that slept some other way cancels the plan.** The lid, the
  power button, an idle timeout — even in the middle of the countdown. A
  plan resumed after that would put the machine straight back to sleep under
  someone who just woke it. Sleep is told apart from a merely *busy* front-end
  by two clocks: the wall clock keeps running while the machine sleeps,
  :func:`awake_clock` does not. A worker stalled on a slow poll moves both
  alike and cancels nothing; it only makes the beat late, never wrong.
* **"Finished" means a turn was seen.** Armed with nothing running, it waits
  for a turn to start and then for every turn to end — so arming first and
  launching the job second works too.

Stdlib only, no UI, no imports from this project at import time — the
Windows suspend call is reached through ``winshell`` lazily, the same way
``attention_hooks`` reaches it.
"""

from __future__ import annotations

import dataclasses
import subprocess
import sys
import time
from dataclasses import dataclass

MODE_FINISHED = "finished"
MODE_DELAY = "delay"

# The menu's choices, in minutes. Presets rather than a time field: the
# Windows tray has no text input, and these cover "after the build",
# "after dinner", "overnight".
DELAY_PRESETS = (30, 60, 120, 240)

# Between "it's time" and actually sleeping: long enough to read the toast
# and reach the menu, short enough not to matter when nobody is there.
GRACE_SECONDS = 60

# Wall-clock time that passed while the awake clock stood still, beyond which
# the machine is taken to have slept. Not zero: the two clocks drift a little,
# and a small forward NTP correction must not read as a sleep.
SLEPT_SECONDS = 15

# Platforms whose front-end path has actually run on a real machine, same
# convention as ``costs.MACOS_READY``. The commands below exist for all three;
# the menu only appears where they have been exercised.
WINDOWS_READY = True
LINUX_READY = False
MACOS_READY = False

SUSPEND_TIMEOUT = 15

EVENT_NONE = ""
EVENT_COUNTDOWN = "countdown"  # grace period opened: tell the user
EVENT_RESUMED = "resumed"  # a terminal went back to work mid-countdown
EVENT_SLEEP = "sleep"  # disarmed: suspend now
EVENT_SLEPT = "slept"  # disarmed: the machine slept some other way meanwhile


@dataclass(frozen=True)
class Plan:
    """What the user armed, and how far along it is.

    ``due`` and ``sleep_at`` are wall-clock (``time.time()``): "in two hours"
    is a promise about the clock on the wall. ``last_wall`` / ``last_awake``
    are the previous beat on each clock, kept only to notice a sleep.
    """

    mode: str
    last_wall: float
    last_awake: float
    due: float | None = None
    seen_busy: bool = False
    sleep_at: float | None = None

    @property
    def counting_down(self) -> bool:
        return self.sleep_at is not None


def after_finish(now: float, awake: float) -> Plan:
    """Sleep once every running turn has ended."""
    return Plan(mode=MODE_FINISHED, last_wall=now, last_awake=awake)


def after_delay(minutes: int, now: float, awake: float) -> Plan:
    """Sleep ``minutes`` from now, whatever the terminals are doing."""
    return Plan(
        mode=MODE_DELAY,
        last_wall=now,
        last_awake=awake,
        due=now + max(1, minutes) * 60,
    )


def advance(
    plan: Plan, now: float, awake: float, busy: bool
) -> tuple[Plan | None, str]:
    """One beat. Returns the next plan (``None`` once disarmed) and an event.

    ``now`` is ``time.time()``, ``awake`` is :func:`awake_clock`, and
    ``busy`` is whether any Claude Code turn is running right now (ignored in
    delay mode).
    """
    slept = (now - plan.last_wall) - (awake - plan.last_awake)
    if slept > SLEPT_SECONDS:
        return None, EVENT_SLEPT
    plan = dataclasses.replace(plan, last_wall=now, last_awake=awake)

    if plan.sleep_at is not None:
        if plan.mode == MODE_FINISHED and busy:
            return dataclasses.replace(plan, sleep_at=None), EVENT_RESUMED
        if now >= plan.sleep_at:
            return None, EVENT_SLEEP
        return plan, EVENT_NONE

    if plan.mode == MODE_DELAY:
        if plan.due is not None and now >= plan.due:
            return _countdown(plan, now)
        return plan, EVENT_NONE

    if busy:
        if not plan.seen_busy:
            plan = dataclasses.replace(plan, seen_busy=True)
        return plan, EVENT_NONE
    if plan.seen_busy:
        return _countdown(plan, now)
    return plan, EVENT_NONE


def _countdown(plan: Plan, now: float) -> tuple[Plan, str]:
    return dataclasses.replace(plan, sleep_at=now + GRACE_SECONDS), EVENT_COUNTDOWN


# ------------------------------------------------------------------- the act


def awake_clock() -> float:
    """Seconds on a clock that stands still while the machine sleeps.

    Linux's ``CLOCK_MONOTONIC`` and macOS's ``mach_absolute_time`` — what
    ``time.monotonic`` reads there — already stop during sleep. Windows is
    the one that needs asking, through ``QueryUnbiasedInterruptTime``; if
    that fails, ``time.monotonic`` is the fallback, and a sleep may then go
    unnoticed (the plan carries on rather than being cancelled).
    """
    if sys.platform == "win32":
        try:
            import winshell

            value = winshell.awake_seconds()
        except Exception:  # noqa: BLE001 — a clock read must not kill the beat
            value = None
        if value is not None:
            return value
    return time.monotonic()


def supported() -> bool:
    """Whether to offer the feature on this platform at all."""
    if sys.platform == "win32":
        return WINDOWS_READY
    if sys.platform == "darwin":
        return MACOS_READY
    return LINUX_READY


def suspend() -> bool:
    """Ask the OS to sleep now. ``True`` if it accepted. Never raises.

    Disarm before calling: on Windows the call may only return once the
    machine is awake again.
    """
    try:
        if sys.platform == "win32":
            import winshell

            return winshell.suspend()
        if sys.platform == "darwin":
            return _run(["pmset", "sleepnow"])
        # logind lets the active local session suspend without a password.
        return _run(["systemctl", "suspend"])
    except Exception:  # noqa: BLE001 — a failed suspend must not kill the beat
        return False


def _run(argv: list[str]) -> bool:
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            timeout=SUSPEND_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0
