"""End-to-end tests for the container entrypoint.

These spawn the real `python -m poketokenweb` process. They are the only tests
that exercise thread composition and signal handling, both of which have
produced defects that unit tests could not see: a shutdown() called from a
signal handler on the serving thread deadlocks, and handlers installed after
the threads start leave a window where SIGTERM is fatal.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _env(tmp_path: Path, port: int, **extra) -> dict:
    env = {
        **os.environ,
        "POKETOKENWEB_DATA_DIR": str(tmp_path / "data"),
        "POKETOKENWEB_WEB_ROOT": str(tmp_path / "dist"),
        "POKETOKENWEB_SPOOL_DIR": str(tmp_path / "spool"),
        "POKETOKENWEB_HOST": "127.0.0.1",
        "PORT": str(port),
        "TZ": "UTC",
        # An empty HOME: no logs, no credentials. The app must still start.
        "HOME": str(tmp_path / "home"),
    }
    # Never inherit a real notification target into a test.
    env.pop("APPRISE_URLS", None)
    env.update(extra)
    return env


def _spawn(tmp_path: Path, port: int, **extra) -> subprocess.Popen:
    (tmp_path / "dist").mkdir(parents=True, exist_ok=True)
    (tmp_path / "dist" / "index.html").write_text("<!doctype html><title>ptw</title>")
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(
        [sys.executable, "-m", "poketokenweb"],
        cwd=REPO,
        env=_env(tmp_path, port, **extra),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _await_health(port: int, proc: subprocess.Popen, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            out, err = proc.communicate()
            raise AssertionError(f"process exited early rc={proc.returncode}\n{err}\n{out}")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as r:
                if r.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            time.sleep(0.25)
    raise AssertionError("server never became healthy")


def _terminate(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.kill()
        proc.wait(timeout=10)


def test_sigterm_exits_cleanly_and_promptly(tmp_path):
    """A handler calling shutdown() from the serving thread would hang here."""
    port = _free_port()
    proc = _spawn(tmp_path, port)
    try:
        _await_health(port, proc)
        started = time.monotonic()
        proc.send_signal(signal.SIGTERM)
        # Generous relative to the entrypoint's own 20s join budget, so a slow
        # in-flight poll is not misreported as a deadlock.
        proc.wait(timeout=45)
        elapsed = time.monotonic() - started
        assert proc.returncode == 0, proc.stderr.read()
        assert elapsed < 45
    finally:
        _terminate(proc)


def _await_marker(proc: subprocess.Popen, timeout: float = 30.0) -> None:
    """Block until the entrypoint reports its signal handlers are live.

    Waiting for the marker rather than sleeping a guessed duration makes this
    test precise: it sends SIGTERM at the earliest moment the app claims to be
    signal-safe, which is exactly the property under test. The window before
    that marker belongs to interpreter startup and cannot be closed in Python.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = proc.stderr.readline()
        if not line:
            if proc.poll() is not None:
                raise AssertionError(f"process exited early rc={proc.returncode}")
            continue
        if "signal handlers installed" in line:
            return
    raise AssertionError("entrypoint never reported signal handlers")


def test_sigterm_at_the_earliest_signal_safe_moment_is_not_fatal(tmp_path):
    """Handlers must be live before the expensive imports, not after them.

    Installing them inside main() left a window of tens of milliseconds -- the
    apprise import alone -- during which SIGTERM killed the process with
    signal 15 instead of exiting 0.
    """
    port = _free_port()
    proc = _spawn(tmp_path, port)
    try:
        _await_marker(proc)
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=45)
        assert proc.returncode == 0, (
            f"rc={proc.returncode} (-15 means SIGTERM took the default action)"
        )
    finally:
        _terminate(proc)


def test_serves_state_and_config_with_an_empty_home(tmp_path):
    """No logs and no credentials must still produce a usable app."""
    port = _free_port()
    proc = _spawn(tmp_path, port)
    try:
        _await_health(port, proc)
        deadline = time.time() + 90
        payload = None
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/state", timeout=5
                ) as r:
                    payload = json.loads(r.read())
                    break
            except urllib.error.HTTPError as exc:
                if exc.code != 503:
                    raise
                time.sleep(1)
        assert payload is not None, "daemon never published state"
        assert payload["companion"]["stage"] == "egg"
        assert payload["today"]["total_tokens"] == 0

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/config", timeout=5) as r:
            assert json.loads(r.read())["refresh_interval"] == 120
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=45)
        finally:
            _terminate(proc)


def test_an_invalid_apprise_url_is_reported_at_startup(tmp_path):
    """A typo must be loud, not silently indistinguishable from 'unset'."""
    port = _free_port()
    proc = _spawn(tmp_path, port, APPRISE_URLS="ntfyy://typo/here")
    try:
        _await_health(port, proc)
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=45)
        stderr = proc.stderr.read()
        assert "ntfyy://typo/here" in stderr or "invalid" in stderr.lower()
    finally:
        _terminate(proc)


def test_signal_handlers_are_installed_before_the_expensive_imports():
    """Structural guard for the startup window.

    The behavioural test above waits for the readiness marker, so it proves
    "SIGTERM is clean once handlers are live" -- but it cannot see WHERE that
    happens, because the marker travels with the handlers. Measured: installing
    them at module scope makes the process signal-safe in 37-48ms, versus
    341-527ms when installed inside main() after `import apprise` builds its
    plugin registry. A timing assertion would flake on a loaded runner, so the
    ordering is asserted structurally instead.
    """
    import ast

    source = (REPO / "poketokenweb" / "__main__.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    install_line = None
    heavy_import_line = None
    for node in tree.body:  # module level only -- inside main() does not count
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr == "signal"
            and isinstance(node.value.func.value, ast.Name)
            and node.value.func.value.id == "signal"
        ):
            install_line = node.lineno if install_line is None else install_line
        if isinstance(node, ast.ImportFrom) and node.level == 1:
            names = {alias.name for alias in node.names}
            if names & {"notify", "runner", "server"}:
                heavy_import_line = (
                    node.lineno if heavy_import_line is None else heavy_import_line
                )

    assert install_line is not None, (
        "signal.signal() must be called at module scope in __main__.py; "
        "installing handlers inside main() leaves a ~350ms window where "
        "SIGTERM kills the process with signal 15"
    )
    assert heavy_import_line is not None, "expected a relative import of notify/runner/server"
    assert install_line < heavy_import_line, (
        f"signal handlers installed at line {install_line}, after the heavy "
        f"imports at line {heavy_import_line}"
    )
