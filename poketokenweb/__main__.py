"""Container entrypoint: the main thread waits, the daemon polls, the web serves.

Thread layout is deliberate and each choice fixes a specific failure:

* The main thread does nothing but block on an Event. ``signal`` handlers run on
  the main thread, and ``ThreadingHTTPServer.shutdown()`` blocks until
  ``serve_forever()`` returns -- so calling shutdown from a handler that
  interrupted serve_forever on that same thread deadlocks, and every rollout
  then waits out its full termination grace period.

* Signal handlers are installed at MODULE IMPORT time, before the heavy imports
  below. Installing them inside main() still left a window of tens of
  milliseconds -- importing apprise alone builds a plugin registry -- during
  which SIGTERM took its default action and the process died with signal 15
  instead of exiting 0. Only stdlib modules that are already loaded may be
  imported above the handler installation.

* The daemon thread builds its own ScanCache. sqlite3 connections are bound to
  the thread that created them, and a cross-thread cache makes every poll raise
  into a swallowed error list -- the app then serves zero tokens while looking
  healthy. See runner.run_loop.
"""

from __future__ import annotations

import os
import signal
import sys
import threading

# --- signal safety, established before anything expensive is imported -------

_STOP = threading.Event()
_SHUTTING_DOWN = threading.Event()
# Printed once handlers are live. Tests wait for this rather than guessing a
# sleep duration, so the "SIGTERM during startup" case is exercised precisely.
READY_MARKER = "signal handlers installed"


def _on_signal(_signum, _frame) -> None:
    _SHUTTING_DOWN.set()


signal.signal(signal.SIGTERM, _on_signal)
signal.signal(signal.SIGINT, _on_signal)
print(READY_MARKER, file=sys.stderr, flush=True)

# --- everything below may be slow to import ---------------------------------

from . import notify, paths as paths_module, runner, server  # noqa: E402


def main() -> int:
    paths = paths_module.resolve()
    try:
        paths.ensure()
    except OSError as exc:
        # The documented PUID/PGID path lands here when the data volume is owned
        # by someone else. A bare traceback in a restart loop tells the operator
        # nothing actionable.
        runner.log(
            f"cannot write POKETOKENWEB_DATA_DIR={paths.state_file.parent.parent} "
            f"as uid {os.getuid()}: {exc}. Chown the volume to that uid, or set "
            f"PUID/PGID to the owner of your log files."
        )
        return 1

    # run_loop logs the notifier's enabled/invalid state; do not duplicate it
    # here. Reporting .invalid matters because a typo'd URI is otherwise
    # indistinguishable from notifications being switched off.
    notifier = notify.from_env()

    daemon_thread = threading.Thread(
        target=runner.run_loop,
        args=(paths, notifier, _STOP),
        name="daemon",
        daemon=True,
    )
    daemon_thread.start()

    httpd = server.build_server(
        paths,
        host=os.environ.get("POKETOKENWEB_HOST") or "0.0.0.0",
        # `or` not a default: Kubernetes renders an unset env var as an empty
        # string, which int("") rejects with a traceback at startup.
        port=int(os.environ.get("PORT") or "8080"),
    )
    web_thread = threading.Thread(
        target=httpd.serve_forever, name="web", daemon=True
    )
    web_thread.start()
    runner.log(f"listening on {httpd.server_address[0]}:{httpd.server_address[1]}")

    _SHUTTING_DOWN.wait()
    runner.log("shutting down")

    _STOP.set()
    httpd.shutdown()
    httpd.server_close()
    # Bounded: a poll in flight can be mid-network-call with its own timeouts,
    # and we would rather exit than hang the pod's termination.
    daemon_thread.join(timeout=20)
    if daemon_thread.is_alive():
        runner.log("daemon thread did not stop within 20s; exiting anyway")
    return 0


if __name__ == "__main__":
    sys.exit(main())
