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

from . import celebration as celebration_text
from . import endpoints, events, heartbeat, scan_roots, species
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

    # Before anything fetches: repoint PokeAPI and the sprite CDN, dropping
    # cached species documents if the host changed -- they embed absolute
    # evolution_chain URLs and would otherwise send us back to the old one.
    where = endpoints.apply(cache_dir=paths.cache_dir)
    for problem in where.rejected:
        # Never silent: falling back to the PUBLIC api is exactly what someone
        # running an offline instance would fail to notice.
        log(f"pokeapi endpoint: {problem} - using the default instead")
    if where.customised:
        log(
            f"pokeapi: rest={where.rest} graphql={where.graphql} "
            f"sprites={where.sprite_root}"
        )

    # Before anything reads the cached base-species index: this rebinds the
    # engine's species cap and drops an index built under a different one.
    cap = species.apply(cache_dir=paths.cache_dir)
    if cap != species.DEFAULT_MAX_SPECIES_ID:
        log(
            f"{species.ENV_NAME}: companion pool capped at species {cap} "
            f"(engine default {species.DEFAULT_MAX_SPECIES_ID}). Species past "
            "649 have no animated sprite and fall back to static art."
        )

    cache = ScanCache(paths.scan_db)
    # Extra transcript roots are dropped silently by the engine when absent,
    # which is indistinguishable from "not configured" -- say so once instead.
    # Logged, never fatal: in a container the mount may legitimately appear late.
    # One list per provider: a Codex rollout under a Claude root is not a Claude
    # transcript, so the two parsers must never be handed the same folder.
    configured: dict[str, list] = {}
    for env_name in scan_roots.ROOT_ENV_NAMES:
        roots = scan_roots.configured_roots(env_name)
        configured[env_name] = roots
        for root in scan_roots.missing_roots(roots):
            log(
                f"{env_name}: {root} is not a directory - "
                "ignoring it. Inside a container this must be the CONTAINER path, "
                "not the host path."
            )
    daemon = Daemon(
        state_path=paths.state_file,
        config_path=paths.config_file,
        cache=cache,
        providers=[
            ClaudeProvider(
                cache=cache, extra_roots=configured[scan_roots.CLAUDE_ROOTS_ENV]
            ),
            CodexProvider(
                cache=cache, extra_roots=configured[scan_roots.CODEX_ROOTS_ENV]
            ),
        ],
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


def _sprite_for(store, species_id: int | None, shiny: bool) -> str | None:
    """Local path to the STATIC sprite, or None if it cannot be had.

    Static rather than the animated GIF: Pushover renders a still frame either
    way, and PNG is the safer common denominator across backends. Never raises
    -- a missing sprite must cost the picture, not the notification.
    """
    if store is None or not isinstance(species_id, int):
        return None
    try:
        path = store.path(species_id, animated=False, shiny=shiny)
    except Exception:
        return None
    return str(path) if path is not None else None


def _announce(
    paths: Paths,
    notifier: Notifier | None,
    raw: dict | None,
    payload: dict | None,
    sprite_store=None,
    now: float | None = None,
) -> None:
    """Persist a celebration and push it. Engine clears it after one poll.

    The engine's own text is a bare sentence; celebration.describe adds the
    rarity, nature, shininess and -- for a graduation -- how long it took, all
    from this same payload. The enriched text is stored as well as pushed, so
    the in-app Recent activity list and the phone say the same thing.
    """
    announcement = celebration_text.describe(raw, payload)
    if announcement is None:
        return

    entry = events.append(
        paths.events_file,
        {
            "kind": announcement.kind,
            "title": announcement.title,
            "detail": announcement.body,
        },
        now=now,
    )
    if entry is None:
        return
    log(f"celebration: {entry['kind']} — {entry['title']}")
    if notifier is not None and notifier.enabled:
        notifier.send(
            entry["title"],
            entry["detail"],
            entry["kind"],
            attach=_sprite_for(sprite_store, announcement.species_id, announcement.shiny),
        )


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
        _announce(
            paths,
            notifier,
            payload.get("celebration"),
            payload,
            getattr(getattr(daemon, "companion_store", None), "sprites", None),
            now,
        )
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
