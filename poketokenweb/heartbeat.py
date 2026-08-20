"""Liveness signal shared by the daemon thread and the web thread.

A leaf module on purpose: the web thread must be able to answer /healthz without
importing anything that can mutate game state, so this pulls in no engine code
at all.

The health rule exists because of a specific failure. The daemon runs on a
background thread; if it dies, the web thread happily keeps serving. An earlier
design gated /healthz on the heartbeat file but treated "no heartbeat yet" as
startup grace, which never expired -- so a daemon that died before its first
poll left the probe green forever and the pod was never restarted. Hence
`healthy()` bounds the no-heartbeat case with a boot deadline.
"""

from __future__ import annotations

import time
from pathlib import Path

# The engine clamps its own refresh interval; these bound what we accept.
MIN_INTERVAL = 30
MAX_INTERVAL = 3600
DEFAULT_INTERVAL = 120

# How long the process may run with no heartbeat at all before it is considered
# dead rather than starting. A cold scan of a large log corpus plus the first
# PokeAPI fetch is seconds, not minutes; this is generous.
BOOT_GRACE_SECONDS = 300.0


def clamp_interval(raw: object) -> int:
    """Coerce a configured refresh interval into the supported range."""
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL
    return max(MIN_INTERVAL, min(MAX_INTERVAL, value))


def stale_after(interval: object) -> float:
    """Seconds without a heartbeat before the daemon is presumed dead.

    Derived from the EFFECTIVE interval, not the clamp ceiling: an earlier
    version used 3 * MAX_INTERVAL, which at the default 120s interval tolerated
    90 consecutive missed polls before reporting anything.
    """
    return max(5.0 * clamp_interval(interval), 600.0)


def write(path: Path, now: float | None = None) -> None:
    """Publish a heartbeat atomically. Never raises."""
    stamp = time.time() if now is None else now
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(str(stamp), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        # A heartbeat we cannot write must not take down the poll loop; the
        # probe will notice the staleness on its own.
        pass


def age(path: Path, now: float | None = None) -> float | None:
    """Seconds since the last heartbeat, or None if there has never been one."""
    try:
        stamped = float(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return (time.time() if now is None else now) - stamped


def healthy(heartbeat_age: float | None, interval: object, boot_age: float) -> bool:
    """The /healthz decision, as a pure function so it can be tested directly.

    heartbeat_age is None until the daemon publishes its first beat.
    """
    if heartbeat_age is None:
        return boot_age < BOOT_GRACE_SECONDS
    return heartbeat_age <= stale_after(interval)
