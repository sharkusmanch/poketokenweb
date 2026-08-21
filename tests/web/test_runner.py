"""Tests for poketokenweb.runner — the background poll loop.

The load-bearing test here is
``test_scan_cache_is_usable_from_the_poll_thread``: it drives a REAL
``ScanCache`` (against a tmp_path DB) through a REAL ``Daemon.poll_once`` on a
REAL thread. A fake cache cannot stand in, because the defect being guarded is
``sqlite3``'s ``check_same_thread`` — a fake has no thread affinity, so an
injected one keeps passing after the build is moved back to the caller's
thread, which is exactly the regression that must go red.

Everything else uses fakes: the loop's control flow (heartbeats, error
logging, celebration fan-out, interval, shutdown, cache close) is independent
of what the engine computes.

No test may touch the network. ``urlopen`` is stubbed out in the integration
test; the rest never construct engine objects at all.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import apprise
import pytest

from poketokenbar.cache import ScanCache
from poketokenbar.daemon import Daemon
from poketokenbar.pokeapi import PokeAPI
from poketokenbar.providers.claude import ClaudeProvider
from poketokenbar.providers.codex import CodexProvider
from poketokenbar.sprites import SpriteStore
from poketokenweb import events, heartbeat, paths as paths_module, runner
from poketokenweb.notify import Notifier


# --- helpers ---------------------------------------------------------------


def make_paths(tmp_path: Path) -> paths_module.Paths:
    resolved = paths_module.resolve(
        {
            paths_module.DATA_DIR_ENV: str(tmp_path / "data"),
            paths_module.SPOOL_DIR_ENV: str(tmp_path / "spool"),
        }
    )
    resolved.ensure()
    return resolved


class FakeDaemon:
    """Stands in for poketokenbar.Daemon in the control-flow tests."""

    def __init__(self, payload: dict | None = None, raises: Exception | None = None,
                 refresh_interval: object = 120) -> None:
        self.payload = payload if payload is not None else {"errors": []}
        self.raises = raises
        self.config_values = {"refresh_interval": refresh_interval}
        self.polls = 0
        self.poll_threads: list[int] = []

    def poll_once(self) -> dict:
        self.polls += 1
        self.poll_threads.append(threading.get_ident())
        if self.raises is not None:
            raise self.raises
        return self.payload


class FakeCache:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class StubApprise:
    """Minimal Apprise stand-in: accepts every URI, records every push."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    def add(self, url: str) -> bool:
        return True

    def notify(self, body: str, title: str, notify_type, attach=None) -> bool:
        self.sent.append(
            {"body": body, "title": title, "notify_type": notify_type, "attach": attach}
        )
        return True


def stub_notifier() -> tuple[Notifier, StubApprise]:
    stub = StubApprise()
    return Notifier(["stub://recorder"], factory=lambda: stub), stub


class RecordingStop(threading.Event):
    """A stop event that records the timeouts it is asked to wait for.

    Sets itself on the first wait so the loop runs exactly one iteration.
    """

    def __init__(self, stop_after: int = 1) -> None:
        super().__init__()
        self.waits: list[float | None] = []
        self._stop_after = stop_after

    def wait(self, timeout=None):  # type: ignore[override]
        self.waits.append(timeout)
        if len(self.waits) >= self._stop_after:
            self.set()
        return True


CELEBRATION = {
    "kind": "hatched",
    "title": "Charmander hatched!",
    "detail": "From a rare egg",
}


# --- log -------------------------------------------------------------------


def test_log_writes_one_timestamped_line_to_stderr(capsys):
    runner.log("hello")
    captured = capsys.readouterr()
    assert captured.out == ""  # stdout is the web server's, not ours
    line = captured.err.strip()
    assert line.endswith("hello")
    assert line.startswith("[")
    assert str(datetime.now().year) in line


# --- build_daemon ----------------------------------------------------------


def test_build_daemon_wires_the_engine_against_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    paths = make_paths(tmp_path)

    daemon, cache = runner.build_daemon(paths)
    try:
        assert isinstance(daemon, Daemon)
        assert isinstance(cache, ScanCache)
        assert daemon.cache is cache
        assert daemon.state_path == paths.state_file
        assert daemon.config_path == paths.config_file
        # Assigned after construction — Daemon.__init__ has no spool argument.
        assert daemon.spool == paths.spool_dir

        kinds = [type(p) for p in daemon.providers]
        assert kinds == [ClaudeProvider, CodexProvider]
        # One cache shared by both providers, and it is the one we must close.
        assert all(p._cache is cache for p in daemon.providers)

        assert daemon.limits_source is not None
        assert daemon.burn is not None
        assert daemon.status_checker is not None
        # notify-send does not exist in the container; pushes go via Apprise.
        assert daemon.notifier is None
    finally:
        cache.close()


def test_build_daemon_never_falls_back_to_home(tmp_path, monkeypatch):
    """HOME is a read-only mount; PokeAPI/SpriteStore default to it."""
    fake_home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(fake_home))
    paths = make_paths(tmp_path)

    daemon, cache = runner.build_daemon(paths)
    try:
        store = daemon.companion_store
        assert store.save_path == paths.save_file
        assert isinstance(store.api, PokeAPI)
        assert store.api.cache_dir == paths.cache_dir
        # The attribute is `.sprites`, not `.sprite_store`.
        assert isinstance(store.sprites, SpriteStore)
        # paths.sprite_dir must stay in lockstep with what SpriteStore derives.
        assert store.sprites.dir == paths.sprite_dir
        assert cache is not None
    finally:
        cache.close()

    assert not (fake_home / ".cache").exists()
    assert paths.scan_db.is_file()


# --- SQLite thread affinity (the central constraint) -----------------------


def _write_claude_log(home: Path) -> int:
    """One assistant turn dated today, worth 100 tokens."""
    session = home / ".claude" / "projects" / "proj"
    session.mkdir(parents=True)
    stamp = datetime.now().astimezone().isoformat()
    line = json.dumps(
        {
            "type": "assistant",
            "timestamp": stamp,
            "requestId": "req-1",
            "message": {
                "id": "msg-1",
                "model": "claude-sonnet-4",
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 20,
                    "cache_creation_input_tokens": 30,
                    "cache_read_input_tokens": 40,
                },
            },
        }
    )
    (session / "session.jsonl").write_text(line + "\n", encoding="utf-8")
    return 100


def test_scan_cache_is_usable_from_the_poll_thread(tmp_path, monkeypatch):
    """A real ScanCache + real poll on a real thread must see zero errors.

    ScanCache holds a sqlite3 connection with check_same_thread=True. Built on
    the caller's thread and used on the loop thread, EVERY provider scan raises
    ProgrammingError, which poll_once swallows into payload["errors"] — 0
    tokens forever behind a green /healthz. So build_daemon must run inside
    run_loop, on the loop thread.
    """
    home = tmp_path / "home"
    home.mkdir()
    expected_tokens = _write_claude_log(home)
    monkeypatch.setenv("HOME", str(home))

    def no_network(*args, **kwargs):
        raise urllib.error.URLError("network disabled in tests")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)

    paths = make_paths(tmp_path)
    notifier, _stub = stub_notifier()
    stop = threading.Event()

    thread = threading.Thread(
        target=runner.run_loop, args=(paths, notifier, stop), daemon=True
    )
    assert threading.get_ident() != 0
    thread.start()
    try:
        deadline = time.monotonic() + 30.0
        while not paths.state_file.is_file() and time.monotonic() < deadline:
            time.sleep(0.02)
    finally:
        stop.set()
        thread.join(timeout=30.0)

    assert not thread.is_alive()
    assert paths.state_file.is_file(), "the poll thread never published state"

    payload = json.loads(paths.state_file.read_text(encoding="utf-8"))
    errors = payload["errors"]
    assert not [e for e in errors if "ProgrammingError" in e or "same thread" in e], errors
    # Positive proof the cache path was actually exercised, not just error-free.
    assert payload["providers"]["claude_code"]["total_tokens"] == expected_tokens
    assert payload["today"]["total_tokens"] == expected_tokens
    assert paths.scan_db.is_file()


def test_daemon_is_built_on_the_thread_that_polls(tmp_path):
    """Unit-level companion to the ScanCache test: same thread, provably."""
    paths = make_paths(tmp_path)
    daemon = FakeDaemon()
    build_threads: list[int] = []

    def build(_paths):
        build_threads.append(threading.get_ident())
        return daemon, FakeCache()

    stop = RecordingStop()
    thread = threading.Thread(
        target=runner.run_loop, args=(paths, None, stop), kwargs={"build": build},
        daemon=True,
    )
    thread.start()
    thread.join(timeout=10.0)

    assert not thread.is_alive()
    assert build_threads and daemon.poll_threads
    assert build_threads[0] == daemon.poll_threads[0]
    assert build_threads[0] != threading.get_ident()


# --- run_once: celebrations ------------------------------------------------


def test_celebration_is_logged_and_pushed(tmp_path, capsys):
    paths = make_paths(tmp_path)
    notifier, stub = stub_notifier()
    daemon = FakeDaemon({"errors": [], "celebration": dict(CELEBRATION)})

    runner.run_once(paths, notifier, daemon, now=1234.0)

    stored = events.read(paths.events_file)
    assert len(stored) == 1
    assert stored[0]["kind"] == "hatched"
    # Enriched, not the engine's bare sentence -- and the STORED text matches
    # what was pushed, so Recent activity and the phone cannot drift.
    assert stored[0]["title"] == "🐣 " + CELEBRATION["title"]
    assert CELEBRATION["detail"] in stored[0]["detail"]
    assert stored[0]["published_at"] == 1234.0

    assert len(stub.sent) == 1
    assert stub.sent[0]["title"] == stored[0]["title"]
    assert stub.sent[0]["body"] == stored[0]["detail"]
    assert stub.sent[0]["notify_type"] == apprise.NotifyType.SUCCESS
    capsys.readouterr()


def test_the_companion_facts_reach_the_push(tmp_path, capsys):
    """Rarity and nature are known at this moment and used to be discarded."""
    paths = make_paths(tmp_path)
    notifier, stub = stub_notifier()
    daemon = FakeDaemon(
        {
            "errors": [],
            "celebration": dict(CELEBRATION),
            "companion": {
                "stage": "mon",
                "species_id": 4,
                "name": "Charmander",
                "rarity": "rare",
                "nature": "relaxed",
                "is_shiny": False,
                "stage_index": 0,
                "total_forms": 3,
            },
        }
    )

    runner.run_once(paths, notifier, daemon, now=1.0)

    body = stub.sent[0]["body"]
    assert "Rare" in body
    assert "relaxed nature" in body
    assert "stage 1 of 3" in body
    capsys.readouterr()


def test_the_static_sprite_is_attached(tmp_path, capsys):
    paths = make_paths(tmp_path)
    notifier, stub = stub_notifier()
    sprite = tmp_path / "4-s.png"
    sprite.write_bytes(b"\x89PNG")

    class Store:
        def __init__(self):
            self.asked = []

        def path(self, species_id, animated=True, shiny=False):
            self.asked.append((species_id, animated, shiny))
            return sprite

    store = Store()
    daemon = FakeDaemon(
        {
            "errors": [],
            "celebration": dict(CELEBRATION),
            "companion": {"stage": "mon", "species_id": 4, "is_shiny": False},
        }
    )
    daemon.companion_store = type("CS", (), {"sprites": store})()

    runner.run_once(paths, notifier, daemon, now=1.0)

    # Static, not the animated GIF: Pushover shows a still frame anyway and
    # PNG is the safer common denominator.
    assert store.asked == [(4, False, False)]
    assert stub.sent[0]["attach"] == str(sprite)
    capsys.readouterr()


def test_a_missing_sprite_costs_the_picture_not_the_push(tmp_path, capsys):
    paths = make_paths(tmp_path)
    notifier, stub = stub_notifier()

    class Store:
        def path(self, species_id, animated=True, shiny=False):
            return None

    daemon = FakeDaemon(
        {
            "errors": [],
            "celebration": dict(CELEBRATION),
            "companion": {"stage": "mon", "species_id": 4},
        }
    )
    daemon.companion_store = type("CS", (), {"sprites": Store()})()

    runner.run_once(paths, notifier, daemon, now=1.0)

    assert len(stub.sent) == 1
    assert stub.sent[0]["attach"] is None
    capsys.readouterr()


def test_a_raising_sprite_store_costs_the_picture_not_the_push(tmp_path, capsys):
    paths = make_paths(tmp_path)
    notifier, stub = stub_notifier()

    class Store:
        def path(self, species_id, animated=True, shiny=False):
            raise OSError("cache volume gone")

    daemon = FakeDaemon(
        {
            "errors": [],
            "celebration": dict(CELEBRATION),
            "companion": {"stage": "mon", "species_id": 4},
        }
    )
    daemon.companion_store = type("CS", (), {"sprites": Store()})()

    runner.run_once(paths, notifier, daemon, now=1.0)

    assert len(stub.sent) == 1
    assert stub.sent[0]["attach"] is None
    capsys.readouterr()


def test_a_shiny_asks_for_the_shiny_sprite(tmp_path, capsys):
    paths = make_paths(tmp_path)
    notifier, _stub = stub_notifier()

    class Store:
        def __init__(self):
            self.asked = []

        def path(self, species_id, animated=True, shiny=False):
            self.asked.append((species_id, animated, shiny))
            return None

    store = Store()
    daemon = FakeDaemon(
        {
            "errors": [],
            "celebration": {"kind": "shiny", "title": "A shiny hatched!", "detail": "d"},
            "companion": {"stage": "mon", "species_id": 25, "is_shiny": True},
        }
    )
    daemon.companion_store = type("CS", (), {"sprites": store})()

    runner.run_once(paths, notifier, daemon, now=1.0)

    assert store.asked == [(25, False, True)]
    capsys.readouterr()


def test_no_celebration_pushes_nothing_and_logs_nothing(tmp_path):
    paths = make_paths(tmp_path)
    notifier, stub = stub_notifier()

    runner.run_once(paths, notifier, FakeDaemon({"errors": []}), now=1.0)
    runner.run_once(
        paths, notifier, FakeDaemon({"errors": [], "celebration": {}}), now=2.0
    )
    runner.run_once(
        paths, notifier, FakeDaemon({"errors": [], "celebration": None}), now=3.0
    )

    assert stub.sent == []
    assert events.read(paths.events_file) == []
    assert not paths.events_file.exists()


def test_celebration_is_still_logged_when_pushes_are_disabled(tmp_path):
    """No APPRISE_URLS is the common case; the event log must not depend on it."""
    paths = make_paths(tmp_path)
    notifier = Notifier([])
    assert not notifier.enabled

    runner.run_once(
        paths, notifier, FakeDaemon({"errors": [], "celebration": dict(CELEBRATION)}),
        now=5.0,
    )

    assert [e["kind"] for e in events.read(paths.events_file)] == ["hatched"]


def test_run_once_survives_a_notifier_of_none(tmp_path):
    paths = make_paths(tmp_path)
    payload = runner.run_once(
        paths, None, FakeDaemon({"errors": [], "celebration": dict(CELEBRATION)}),
        now=7.0,
    )
    assert payload is not None
    assert len(events.read(paths.events_file)) == 1


# --- run_once: errors and the heartbeat ------------------------------------


def test_payload_errors_are_logged(tmp_path, capsys):
    paths = make_paths(tmp_path)
    daemon = FakeDaemon({"errors": ["claude_code: boom", "limits: needs login"]})

    runner.run_once(paths, None, daemon, now=1.0)

    err = capsys.readouterr().err
    assert "claude_code: boom" in err
    assert "limits: needs login" in err


def test_heartbeat_advances_on_a_successful_poll(tmp_path):
    paths = make_paths(tmp_path)
    daemon = FakeDaemon({"errors": []})

    runner.run_once(paths, None, daemon, now=1000.0)
    assert heartbeat.age(paths.heartbeat_file, now=1000.0) == 0.0

    runner.run_once(paths, None, daemon, now=2000.0)
    assert heartbeat.age(paths.heartbeat_file, now=2000.0) == 0.0
    assert heartbeat.age(paths.heartbeat_file, now=2500.0) == 500.0


def test_heartbeat_is_written_even_when_poll_once_raises(tmp_path, capsys):
    """The failure that made /healthz lie: no beat at all reads as boot grace."""
    paths = make_paths(tmp_path)
    daemon = FakeDaemon(raises=RuntimeError("provider exploded"))

    payload = runner.run_once(paths, None, daemon, now=4242.0)

    assert payload is None
    assert paths.heartbeat_file.is_file()
    assert heartbeat.age(paths.heartbeat_file, now=4242.0) == 0.0
    assert "provider exploded" in capsys.readouterr().err


def test_run_once_never_propagates_a_poll_failure(tmp_path, capsys):
    paths = make_paths(tmp_path)
    for boom in (RuntimeError("kaboom"), KeyError("missing"), OSError("disk gone")):
        assert runner.run_once(paths, None, FakeDaemon(raises=boom), now=1.0) is None
    assert capsys.readouterr().err.count("poll failed") == 3


def test_run_once_returns_the_payload_and_still_beats_when_the_event_log_fails(
    tmp_path, monkeypatch, capsys
):
    """A broken event log must not cost us the heartbeat."""
    paths = make_paths(tmp_path)

    def boom(*args, **kwargs):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(events, "append", boom)
    daemon = FakeDaemon({"errors": [], "celebration": dict(CELEBRATION)})

    runner.run_once(paths, None, daemon, now=99.0)

    assert heartbeat.age(paths.heartbeat_file, now=99.0) == 0.0
    assert "read-only filesystem" in capsys.readouterr().err


# --- run_loop --------------------------------------------------------------


def test_loop_beats_before_building_so_a_build_failure_is_distinguishable(
    tmp_path, capsys
):
    paths = make_paths(tmp_path)
    seen: dict[str, bool] = {}

    def build(_paths):
        seen["beat_before_build"] = paths.heartbeat_file.is_file()
        raise RuntimeError("no such database")

    runner.run_loop(paths, None, threading.Event(), build=build)  # must not raise

    assert seen["beat_before_build"] is True
    assert paths.heartbeat_file.is_file()
    assert "daemon build failed" in capsys.readouterr().err


def test_loop_survives_a_raising_poll_and_keeps_beating(tmp_path, capsys):
    paths = make_paths(tmp_path)
    daemon = FakeDaemon(raises=RuntimeError("every poll fails"))
    cache = FakeCache()
    stop = RecordingStop(stop_after=3)

    runner.run_loop(paths, None, stop, build=lambda _p: (daemon, cache))

    assert daemon.polls == 3  # the loop did not die on the first failure
    assert cache.closed
    err = capsys.readouterr().err
    assert err.count("every poll fails") == 3
    assert paths.heartbeat_file.is_file()


@pytest.mark.parametrize(
    "raw,expected",
    [
        (120, 120),
        (5, heartbeat.MIN_INTERVAL),
        (99999, heartbeat.MAX_INTERVAL),
        ("abc", heartbeat.DEFAULT_INTERVAL),
        (None, heartbeat.DEFAULT_INTERVAL),
    ],
)
def test_interval_clamping_is_delegated_to_heartbeat(tmp_path, raw, expected):
    paths = make_paths(tmp_path)
    daemon = FakeDaemon({"errors": []}, refresh_interval=raw)
    stop = RecordingStop()

    # The sleep is sliced so a queued UI command can cut it short, so capture
    # the interval handed to the sleeper rather than the shape of the waits.
    seen: list[float] = []
    original = runner._sleep_until_due
    runner._sleep_until_due = lambda p, st, iv: (seen.append(iv), st.set())[0]
    try:
        runner.run_loop(paths, None, stop, build=lambda _p: (daemon, FakeCache()))
    finally:
        runner._sleep_until_due = original

    assert seen == [heartbeat.clamp_interval(raw)] == [expected]


def test_loop_does_not_poll_when_stop_is_already_set(tmp_path):
    paths = make_paths(tmp_path)
    daemon = FakeDaemon({"errors": []})
    cache = FakeCache()
    stop = threading.Event()
    stop.set()

    runner.run_loop(paths, None, stop, build=lambda _p: (daemon, cache))

    assert daemon.polls == 0
    assert cache.closed


def test_stop_ends_the_loop_promptly(tmp_path):
    """Real thread, real Event, real timeout — a hung shutdown fails the test."""
    paths = make_paths(tmp_path)
    polled = threading.Event()

    class SignallingDaemon(FakeDaemon):
        def poll_once(self):
            payload = super().poll_once()
            polled.set()
            return payload

    # An hour between polls: only stop.wait() waking on set() can end this.
    daemon = SignallingDaemon({"errors": []}, refresh_interval=3600)
    cache = FakeCache()
    stop = threading.Event()

    thread = threading.Thread(
        target=runner.run_loop,
        args=(paths, None, stop),
        kwargs={"build": lambda _p: (daemon, cache)},
        # daemon=True so a loop that ignores `stop` fails the assert below
        # instead of wedging the interpreter at exit.
        daemon=True,
    )
    thread.start()
    assert polled.wait(timeout=10.0), "the loop never polled"

    started = time.monotonic()
    stop.set()
    thread.join(timeout=10.0)
    elapsed = time.monotonic() - started

    assert not thread.is_alive(), "run_loop ignored stop"
    assert elapsed < 5.0
    assert cache.closed


def test_cache_is_closed_on_exit(tmp_path):
    paths = make_paths(tmp_path)
    cache = FakeCache()

    runner.run_loop(
        paths, None, RecordingStop(), build=lambda _p: (FakeDaemon({"errors": []}), cache)
    )

    assert cache.closed


def test_cache_is_closed_even_if_the_loop_body_explodes(tmp_path):
    """cache.close() lives in a finally, so an unforeseen raise still frees it."""
    paths = make_paths(tmp_path)
    cache = FakeCache()

    class ExplodingStop(threading.Event):
        def wait(self, timeout=None):  # type: ignore[override]
            raise RuntimeError("interrupted")

    with pytest.raises(RuntimeError):
        runner.run_loop(
            paths, None, ExplodingStop(),
            build=lambda _p: (FakeDaemon({"errors": []}), cache),
        )

    assert cache.closed


def test_loop_reports_rejected_apprise_uris(tmp_path, capsys):
    """An unlogged typo is indistinguishable from 'not configured'."""
    paths = make_paths(tmp_path)

    class RejectingApprise(StubApprise):
        def add(self, url: str) -> bool:
            return False

    notifier = Notifier(["definitely-not-a-uri"], factory=RejectingApprise)
    assert notifier.invalid == ["definitely-not-a-uri"]

    runner.run_loop(
        paths, notifier, RecordingStop(),
        build=lambda _p: (FakeDaemon({"errors": []}), FakeCache()),
    )

    err = capsys.readouterr().err
    assert "definitely-not-a-uri" in err
    assert "disabled" in err


class NeverSetStop(threading.Event):
    """Records slice durations and never fires, so the sleeper runs to term."""

    def __init__(self) -> None:
        super().__init__()
        self.waits: list[float] = []

    def wait(self, timeout=None):  # type: ignore[override]
        self.waits.append(timeout)
        return False


def test_a_queued_command_cuts_the_sleep_short(tmp_path):
    """A Buy tap must not wait out the whole refresh interval.

    A flat wait(interval) left a purchase unapplied for up to 120s by default
    (an hour at the maximum), while the UI re-read the pre-command state and the
    user tapped again. The engine's own Daemon.run slices for this reason.
    """
    paths = make_paths(tmp_path)
    paths.spool_dir.mkdir(parents=True, exist_ok=True)
    (paths.spool_dir / "0001.json").write_text('{"name": "refresh", "args": {}}')
    stop = NeverSetStop()

    runner._sleep_until_due(paths, stop, 3600)

    assert sum(stop.waits) <= runner.COMMAND_POLL_SECONDS, (
        f"slept {sum(stop.waits)}s with a command queued; the interval was 3600"
    )


def test_an_empty_spool_sleeps_the_whole_interval(tmp_path):
    paths = make_paths(tmp_path)
    paths.spool_dir.mkdir(parents=True, exist_ok=True)
    stop = NeverSetStop()

    runner._sleep_until_due(paths, stop, 30)

    assert sum(stop.waits) == 30
    assert max(stop.waits) <= runner.COMMAND_POLL_SECONDS


def test_a_set_stop_ends_the_sleep_immediately(tmp_path):
    paths = make_paths(tmp_path)
    paths.spool_dir.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()
    stop.set()

    started = time.monotonic()
    runner._sleep_until_due(paths, stop, 3600)

    assert time.monotonic() - started < 1.0


def test_a_failing_poll_does_not_refresh_the_success_stamp(tmp_path):
    """The defect this exists for: /healthz green while nothing is published.

    A poll that raises still beats -- that keeps 'never started' distinct from
    'running but failing' -- but it must not look like a successful publish.
    """
    paths = make_paths(tmp_path)

    class Exploding(FakeDaemon):
        def poll_once(self):
            self.polls += 1
            raise RuntimeError("scan blew up")

    runner.run_once(paths, None, Exploding({"errors": []}), now=1000.0)

    assert heartbeat.age(paths.heartbeat_file, now=1000.0) == 0.0
    assert heartbeat.success_age(paths.heartbeat_file, now=1000.0) is None


def test_a_successful_poll_records_the_publish(tmp_path):
    paths = make_paths(tmp_path)
    runner.run_once(paths, None, FakeDaemon({"errors": []}), now=1000.0)
    assert heartbeat.success_age(paths.heartbeat_file, now=1000.0) == 0.0


def test_a_later_failure_carries_the_earlier_success_forward(tmp_path):
    paths = make_paths(tmp_path)

    class Exploding(FakeDaemon):
        def poll_once(self):
            raise RuntimeError("boom")

    runner.run_once(paths, None, FakeDaemon({"errors": []}), now=1000.0)
    runner.run_once(paths, None, Exploding({"errors": []}), now=1100.0)

    # Still beating, but the app has published nothing for 100s.
    assert heartbeat.age(paths.heartbeat_file, now=1100.0) == 0.0
    assert heartbeat.success_age(paths.heartbeat_file, now=1100.0) == 100.0
