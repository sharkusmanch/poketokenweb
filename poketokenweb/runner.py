"""The poll loop: engine wiring, one-shot polling, and the background thread.

Three failure modes shaped this module; each one produced an app that looked
healthy while serving nothing.

**SQLite thread affinity.** ``poketokenbar.cache.ScanCache`` wraps
``sqlite3.connect(...)`` with the default ``check_same_thread=True``. Build the
cache on one thread and use it on another and *every* provider scan raises
``sqlite3.ProgrammingError`` — which ``Daemon.poll_once`` dutifully catches
per-provider and folds into ``payload["errors"]``. The poll "succeeds", the
heartbeat advances, /healthz is green, and the page shows 0 tokens forever.
Hence :func:`build_daemon` is called *by* :func:`run_loop`, on the loop thread,
and must never be called by whoever starts the thread.

**The heartbeat is written unconditionally.** An earlier version returned early
when ``poll_once`` raised, so a daemon whose every poll blew up wrote no
heartbeat at all — and ``heartbeat.healthy()`` reads "no heartbeat" as boot
grace. The probe stayed green while nothing worked. :func:`run_once` writes the
beat in a ``finally``.

**Nothing here may propagate out of the thread.** A raise in the loop body ends
the thread silently; the web thread keeps serving stale state with no error
anywhere. So the poll is wrapped, and a failed *build* is logged before the
thread ends — the heartbeat written before the build then stops advancing,
which is exactly what the liveness probe detects.
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Callable

from poketokenbar.burn import BurnTracker
from poketokenbar.cache import ScanCache
from poketokenbar.companion_store import CompanionStore
from poketokenbar.daemon import Daemon
from poketokenbar.limits_source import LimitsSource
from poketokenbar.pokeapi import PokeAPI
from poketokenbar.providers.claude import ClaudeProvider
from poketokenbar.providers.codex import CodexProvider
from poketokenbar.sprites import SpriteStore
from poketokenbar.status import StatusChecker

from . import events, heartbeat
from .notify import Notifier
from .paths import Paths


def log(message: str) -> None:
    """One timestamped line on stderr — the container's only log sink."""
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    print(f"[{stamp}] {message}", file=sys.stderr, flush=True)


def build_daemon(paths: Paths) -> tuple[Daemon, ScanCache]:
    """Wire the engine against ``paths``. CALL THIS ON THE POLL THREAD ONLY.

    The returned cache is handed back separately because the caller owns its
    lifetime: it must be closed on the same thread that created it.
    """
    paths.ensure()
    cache = ScanCache(paths.scan_db)
    daemon = Daemon(
        state_path=paths.state_file,
        config_path=paths.config_file,
        cache=cache,
        providers=[ClaudeProvider(cache=cache), CodexProvider(cache=cache)],
        limits_source=LimitsSource(),
        companion_store=CompanionStore(
            save_path=paths.save_file,
            # Both classes default to ``Path.home()/".cache"``, and HOME is a
            # read-only mount here — the explicit cache_dir is not optional.
            api=PokeAPI(cache_dir=paths.cache_dir),
            sprite_store=SpriteStore(cache_dir=paths.cache_dir),
        ),
        # The engine notifier shells out to `notify-send`, which does not exist
        # in a container. Our pushes go out through the Apprise Notifier below.
        notifier=None,
        burn_tracker=BurnTracker(),
        status_checker=StatusChecker(),
    )
    # Assigned after construction; Daemon.__init__ takes no spool argument.
    daemon.spool = paths.spool_dir
    return daemon, cache


def _announce(
    paths: Paths,
    notifier: Notifier | None,
    celebration: dict | None,
    now: float | None,
) -> None:
    """Persist a celebration and push it. Engine clears it after one poll."""
    entry = events.append(paths.events_file, celebration, now=now)
    if entry is None:
        return
    log(f"celebration: {entry['kind']} — {entry['title']}")
    if notifier is not None and notifier.enabled:
        notifier.send(entry["title"], entry["detail"], entry["kind"])


def run_once(
    paths: Paths,
    notifier: Notifier | None,
    daemon: Daemon,
    now: float | None = None,
) -> dict | None:
    """Poll once, publish what came back, and always beat. Never raises."""
    payload: dict | None = None
    succeeded = False
    try:
        payload = daemon.poll_once()
        succeeded = True
        for error in payload.get("errors") or []:
            log(f"poll error: {error}")
        _announce(paths, notifier, payload.get("celebration"), now)
    except Exception as exc:  # the thread must survive any poll
        log(f"poll failed: {type(exc).__name__}: {exc}")
    finally:
        # Always beat, so "never started" stays distinguishable from "running
        # but failing" -- but only a poll that actually returned refreshes the
        # success stamp. Health is judged on the success stamp: a daemon whose
        # every poll raises publishes nothing, and reporting it healthy left
        # /api/state answering 503 forever with no probe noticing.
        heartbeat.write(paths.heartbeat_file, now, succeeded=succeeded)
    return payload


# How often the sleep wakes to look for queued UI commands. The engine's own
# Daemon.run uses the same trick for the same reason: a flat wait(interval)
# makes a Buy tap sit unapplied for up to the whole refresh interval -- 120s by
# default, an hour at the maximum -- while the UI re-reads the pre-command state
# and the user taps again.
COMMAND_POLL_SECONDS = 2.0


def _has_queued_commands(paths: Paths) -> bool:
    try:
        return any(paths.spool_dir.glob("*.json"))
    except OSError:
        return False


def _sleep_until_due(paths: Paths, stop: threading.Event, interval: float) -> None:
    """Wait out the refresh interval, but wake early for a queued command."""
    waited = 0.0
    while waited < interval:
        slice_seconds = min(COMMAND_POLL_SECONDS, interval - waited)
        if stop.wait(slice_seconds):
            return
        waited += slice_seconds
        if _has_queued_commands(paths):
            return


def run_loop(
    paths: Paths,
    notifier: Notifier | None,
    stop: threading.Event,
    build: Callable[[Paths], tuple[Daemon, ScanCache]] = build_daemon,
) -> None:
    """Poll until ``stop`` is set. Runs as the app's single background thread."""
    # Before the build, so "never started" and "build failed" are different
    # states to the probe: a build failure leaves this one beat frozen in time.
    heartbeat.write(paths.heartbeat_file)

    if notifier is not None:
        for url in notifier.invalid:
            # Unlogged, a typo'd URI is indistinguishable from "not configured".
            log(f"apprise: rejected URI {url!r}")
        log(f"apprise: push notifications {'enabled' if notifier.enabled else 'disabled'}")

    try:
        daemon, cache = build(paths)
    except Exception as exc:
        log(f"daemon build failed: {type(exc).__name__}: {exc}")
        return

    log("daemon started")
    try:
        while not stop.is_set():
            run_once(paths, notifier, daemon)
            interval = heartbeat.clamp_interval(
                daemon.config_values.get("refresh_interval")
            )
            _sleep_until_due(paths, stop, interval)
    finally:
        # Same thread that opened it, as sqlite3 requires.
        cache.close()
        log("daemon stopped")
