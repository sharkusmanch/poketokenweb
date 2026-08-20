"""Liveness signal shared by the daemon thread and the web thread.

A leaf module on purpose: the web thread must be able to answer /healthz without
importing anything that can mutate game state, so this pulls in no engine code
at all.

The health rule exists because of two specific failures, in the order they were
found:

* A daemon that dies before its first poll never writes a heartbeat. Treating
  "no heartbeat" as startup grace forever left the probe green indefinitely, so
  the pod was never restarted. Hence the boot deadline.

* A daemon whose every poll RAISES is just as broken, but it keeps beating. An
  earlier version wrote the beat in a `finally` and reported healthy on that
  alone, so an app publishing nothing at all -- /api/state answering 503
  forever -- passed every probe. Hence the beat records the last SUCCESSFUL
  publish alongside the last attempt, and health is judged on the former.

Both values live in one file so a single atomic write keeps them consistent.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

# The engine clamps its own refresh interval; these bound what we accept.
MIN_INTERVAL = 30
MAX_INTERVAL = 3600
DEFAULT_INTERVAL = 120

# How long the process may run without a successful publish before it is
# considered dead rather than starting. A cold scan of a large corpus plus the
# first PokeAPI fetch is seconds, not minutes; this is generous.
BOOT_GRACE_SECONDS = 300.0


def clamp_interval(raw: object) -> int:
    """Coerce a configured refresh interval into the supported range."""
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL
    return max(MIN_INTERVAL, min(MAX_INTERVAL, value))


def stale_after(interval: object) -> float:
    """Seconds without a successful publish before the daemon is presumed dead.

    Derived from the EFFECTIVE interval, not the clamp ceiling: an earlier
    version used 3 * MAX_INTERVAL, which at the default 120s interval tolerated
    90 consecutive missed polls before reporting anything.
    """
    return max(5.0 * clamp_interval(interval), 600.0)


def write(path: Path, now: float | None = None, *, succeeded: bool = False) -> None:
    """Record a poll attempt, and the time of the last successful publish.

    A failed poll still beats -- that is what keeps "never started" and "running
    but failing" distinguishable -- but it carries the PREVIOUS success time
    forward rather than refreshing it. Never raises: a heartbeat we cannot write
    must not take down the poll loop.
    """
    stamp = time.time() if now is None else now
    previous = _read(path)
    last_success = stamp if succeeded else previous.get("ok_at")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # A unique temp name: a fixed one lets two writers interleave into the
        # same file and rename a torn result into place.
        tmp = path.with_name(f"{path.name}.{time.time_ns()}.tmp")
        tmp.write_text(json.dumps({"beat": stamp, "ok_at": last_success}), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def _read(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    if isinstance(data, dict):
        return data
    # The pre-JSON format was a bare float, and json.loads parses that happily
    # -- so an upgrade in place would otherwise read as "never started" and
    # restart the pod once for no reason. No recorded success, by definition.
    if isinstance(data, (int, float)) and not isinstance(data, bool):
        return {"beat": float(data), "ok_at": None}
    return {}


def age(path: Path, now: float | None = None) -> float | None:
    """Seconds since the last poll ATTEMPT, or None if there has never been one."""
    beat = _read(path).get("beat")
    if not isinstance(beat, (int, float)):
        return None
    return (time.time() if now is None else now) - beat


def success_age(path: Path, now: float | None = None) -> float | None:
    """Seconds since the last SUCCESSFUL publish, or None if there never was one."""
    ok_at = _read(path).get("ok_at")
    if not isinstance(ok_at, (int, float)):
        return None
    return (time.time() if now is None else now) - ok_at


def healthy(
    heartbeat_age: float | None,
    interval: object,
    boot_age: float,
    publish_age: float | None = None,
) -> bool:
    """The /healthz decision, as a pure function so it can be tested directly.

    `heartbeat_age` is None until the daemon attempts its first poll.
    `publish_age` is None until one of those polls SUCCEEDS -- a daemon that is
    beating but has never published is only healthy during boot grace.
    """
    if heartbeat_age is None:
        return boot_age < BOOT_GRACE_SECONDS
    if publish_age is None:
        # Beating but never published: still starting, or failing every poll.
        return boot_age < BOOT_GRACE_SECONDS
    return publish_age <= stale_after(interval)
