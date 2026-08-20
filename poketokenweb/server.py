"""The read-mostly HTTP face of the app: the WEB thread.

ARCHITECTURAL RULE (load-bearing, not style)
    Nothing here may import ``CompanionStore``, ``save``, the runner, or any
    other engine module that mutates game state. The daemon thread owns that
    state; two threads writing ``companion.json`` is how a save gets shredded.
    This module therefore has exactly two write channels:

        * ``commands.enqueue`` — drops one file into a spool the daemon drains
        * ``config.set_value`` — an atomic temp+rename on config.json

    Everything else it does is a read. Keep it that way when adding routes: a
    new "do the thing now" endpoint is a new command name, not a new import.

Four defects that shipped in an earlier design, and the shape of their fixes:

1.  KEEP-ALIVE DESYNC. With ``protocol_version = "HTTP/1.1"`` the connection is
    reused. Rejecting a POST for a bad Content-Type *without reading its body*
    leaves those bytes in the socket, and the next request on that connection is
    parsed from them — a reviewer got ``POST /api/command`` -> 400 followed by
    ``GET /healthz`` -> ``501 Unsupported method ('{"name":"refresh"}')``.
    ``_read_body()`` therefore drains Content-Length bytes BEFORE any validation
    check, and closes the connection outright in the cases it cannot drain
    (chunked, unparseable or oversized). Note that a test built on
    ``urllib.request.urlopen`` cannot observe this at all: urllib opens a fresh
    connection per call. The regression test uses one ``http.client``
    connection for two sequential requests.

2.  UNHANDLED EXCEPTION TYPES. ``do_POST`` catching only
    ``(ValueError, KeyError, JSONDecodeError)`` drops the connection with *no
    response* and prints a traceback full of server paths the moment anything
    else goes wrong (an OSError writing the spool, a full disk). The outer
    ``except Exception`` here is the last line of defence and returns 500 JSON.

3.  ``_static`` EXCEPTION COVERAGE. Wrapping only ``.resolve()`` is not enough:
    a 5000-character path raises ENAMETOOLONG out of ``Path.is_file()``, which
    does not ignore that errno, and that call sits outside such a try. The whole
    handler body is wrapped instead.

4.  CACHE-CONTROL FROM THE REQUEST ROUTE. ``IMMUTABLE if
    route.startswith("/assets/")`` serves ``GET /assets/../index.html`` — the
    SPA shell — with a one-year immutable header, stranding every client that
    got it across the next deploy. The header is chosen from the RESOLVED path
    relative to web_root, and only a content-hashed bundle may be immutable.
"""

from __future__ import annotations

import json
import mimetypes
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from poketokenbar import commands, config

from . import api, events, heartbeat
from .paths import Paths

#: Largest request body accepted. Every legitimate POST here is a handful of
#: bytes; anything larger is rejected before it is read, so a client cannot make
#: the server buffer a gigabyte.
MAX_BODY_BYTES = 64 * 1024

#: Read timeout per connection. Set on the handler class — NOT via
#: socket.setdefaulttimeout(), which mutates process-global state from a
#: constructor and would not affect an already-created listening socket anyway.
CONNECTION_TIMEOUT = 30.0

JSON_TYPE = "application/json; charset=utf-8"

# Cache policies.
NO_STORE = "no-store"
REVALIDATE = "no-cache"
IMMUTABLE = "public, max-age=31536000, immutable"
#: Sprites are content-addressed by (species, form, shiny) and rewritten under a
#: new name when they change, so they cache long — but not "immutable", because
#: a corrupted download should be recoverable by a reload.
SPRITE_CACHE = "public, max-age=2592000"

#: Only files under this directory are eligible to be immutable.
HASHED_ASSET_DIR = "assets"


def _is_content_hashed(filename: str) -> bool:
    """True when the filename carries a build hash (``index-DdXtT1nq.js``).

    A token of 8+ alphanumerics mixing letters and digits. Deliberately
    conservative: a false negative costs one revalidation round-trip, while a
    false positive pins a stale bundle in browser caches for a year.
    """
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    tokens = re.split(r"[.\-_]", stem)
    # The first token is the entry name (``index``), never the hash; requiring a
    # separator also means a bare ``bundle.js`` is not mistaken for hashed.
    for token in tokens[1:]:
        if len(token) >= 8 and token.isalnum() and _looks_random(token):
            return True
    return False


def _looks_random(token: str) -> bool:
    """Heuristic for a build hash rather than a word.

    Vite emits base64url-ish hashes that frequently contain NO digit at all
    (observed: ``index-DmWNSQuv.css``), so an ``any(isdigit)`` test rejects a
    real hashed asset and pins it to no-cache. Mixed case or a digit is enough
    signal here, because only files directly under assets/ — a directory the
    bundler owns entirely — are ever eligible.
    """
    has_digit = any(c.isdigit() for c in token)
    mixed_case = any(c.islower() for c in token) and any(c.isupper() for c in token)
    return has_digit or mixed_case


def _has_extension(relative: str) -> bool:
    """True when the last path segment looks like a file rather than a route."""
    return "." in relative.rsplit("/", 1)[-1]


def _message(exc: BaseException) -> str:
    """One safe line for a rejected request.

    KeyError stringifies to ``"'refresh_interval'"``; args[0] keeps it readable.
    Everything is run through api.sanitize_error so an engine error that
    embeds a filesystem path cannot reach the browser.
    """
    text = str(exc.args[0]) if exc.args else exc.__class__.__name__
    return api.sanitize_error(text)


class _Handler(BaseHTTPRequestHandler):
    # Keep-alive: browsers reuse the connection, so every early rejection has to
    # leave the socket in a known state. See defect 1 in the module docstring.
    protocol_version = "HTTP/1.1"
    server_version = "poketokenweb"
    sys_version = ""  # do not advertise the Python version
    timeout = CONNECTION_TIMEOUT

    # --- plumbing ---------------------------------------------------------

    def log_message(self, fmt: str, *args) -> None:  # pragma: no cover - noise
        """Silence the default stderr access log; it is not this app's log."""

    @property
    def _paths(self) -> Paths:
        return self.server.paths  # type: ignore[attr-defined]

    def _respond(
        self,
        status: int,
        body: bytes,
        content_type: str,
        cache: str,
        extra: tuple[tuple[str, str], ...] = (),
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in extra:
            self.send_header(key, value)
        self.end_headers()
        # HEAD must carry identical headers (including Content-Length) but no
        # body. Writing one would desynchronise a keep-alive connection just as
        # surely as an undrained request body does.
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload, cache: str = NO_STORE) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._respond(status, body, JSON_TYPE, cache)

    def _not_found(self) -> None:
        self._json(404, {"error": "not found"})

    # --- request body -----------------------------------------------------

    def _drain(self, length: int) -> bytes:
        """Read exactly `length` bytes off the socket, in bounded chunks."""
        chunks: list[bytes] = []
        remaining = length
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 65536))
            if not chunk:
                # Client vanished mid-body; the connection is unusable.
                self.close_connection = True
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _read_body(self) -> tuple[bytes | None, tuple[int, str] | None]:
        """(body, None) or (None, (status, message)).

        THE ORDER MATTERS. The body is drained before the Content-Type check,
        so a rejected POST never leaves bytes in the socket for the next request
        on the same keep-alive connection to be parsed from (defect 1).
        """
        if self.headers.get("Transfer-Encoding", "").strip():
            # Chunked framing: we would have to decode it to know where the
            # body ends, so the only safe rejection is to close.
            self.close_connection = True
            return None, (411, "chunked request bodies are not supported")

        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            length = 0  # no framing header and not chunked => no body
        else:
            try:
                length = int(raw_length)
            except (TypeError, ValueError):
                self.close_connection = True
                return None, (400, "invalid Content-Length")
            if length < 0:
                self.close_connection = True
                return None, (400, "invalid Content-Length")

        if length > MAX_BODY_BYTES:
            # Too big to drain politely: rejecting without reading means the
            # connection cannot be reused, so say so and close.
            self.close_connection = True
            return None, (413, "request body too large")

        body = self._drain(length)

        media = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if media and media != "application/json":
            # Body already drained above — connection stays usable.
            return None, (415, "expected application/json")
        return body, None

    def _discard_body(self) -> None:
        """Swallow the body of a request we are about to refuse."""
        _body, error = self._read_body()
        del _body, error

    # --- POST -------------------------------------------------------------

    def do_HEAD(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler protocol
        """Route exactly as GET; _respond suppresses the body.

        Without this BaseHTTPRequestHandler answers 501, which breaks uptime
        checkers, some reverse-proxy probes, and any client that HEADs a static
        asset before fetching it.
        """
        self.do_GET()

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler protocol
        try:
            route = urlparse(self.path).path
            handler = {
                "/api/command": self._post_command,
                "/api/config": self._post_config,
            }.get(route)
            if handler is None:
                self._discard_body()
                self._not_found()
                return

            body, error = self._read_body()
            if error is not None:
                self._json(error[0], {"error": error[1]})
                return

            try:
                payload = json.loads((body or b"").decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._json(400, {"error": "body must be JSON"})
                return

            try:
                result = handler(payload)
            except (api.ValidationError, ValueError, KeyError) as exc:
                self._json(400, {"error": _message(exc)})
                return
            self._json(202, result)
        except Exception:
            # Defect 2: anything at all — an OSError writing the spool, a full
            # disk — must become a 500 with a generic body, never a dropped
            # connection plus a traceback naming server paths.
            try:
                self._json(500, {"error": "internal error"})
            except Exception:  # pragma: no cover - socket already gone
                self.close_connection = True

    def _post_command(self, payload) -> dict:
        name, args = api.validate_command(payload)
        commands.enqueue(name, args, spool=self._paths.spool_dir)
        return {"status": "accepted", "name": name, "args": args}

    def _post_config(self, payload) -> dict:
        key, value = api.validate_config(payload)
        config.set_value(self._paths.config_file, key, value)
        # The daemon holds the live settings in memory; the file alone would not
        # take effect until a restart.
        commands.enqueue("reload_config", {}, spool=self._paths.spool_dir)
        return {"status": "accepted", "key": key, "value": value}

    # --- GET --------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler protocol
        try:
            route = urlparse(self.path).path
            if route == "/healthz":
                self._healthz()
            elif route == "/api/state":
                self._state()
            elif route == "/api/events":
                self._events()
            elif route == "/api/config":
                self._config()
            elif route.startswith("/sprites/"):
                self._sprite(route)
            else:
                self._static(route)
        except Exception:
            try:
                self._json(500, {"error": "internal error"})
            except Exception:  # pragma: no cover - socket already gone
                self.close_connection = True

    def _healthz(self) -> None:
        """Liveness for the whole pod, not just this thread.

        The web thread being able to answer proves nothing about the daemon
        thread that actually refreshes usage — an earlier design that returned
        200 whenever the socket accepted left dead daemons running forever. The
        decision is heartbeat.healthy(), which also bounds the
        never-had-a-heartbeat case with the boot deadline.
        """
        settings = config.load(self._paths.config_file)
        interval = heartbeat.clamp_interval(settings.get("refresh_interval"))
        now = time.time()
        beat_age = heartbeat.age(self._paths.heartbeat_file, now)
        boot_age = now - self.server.boot_time  # type: ignore[attr-defined]
        ok = heartbeat.healthy(beat_age, interval, boot_age)
        self._json(
            200 if ok else 503,
            {
                "ok": ok,
                "heartbeat_age": beat_age,
                "interval": interval,
                "boot_age": boot_age,
            },
        )

    def _state(self) -> None:
        try:
            payload = json.loads(
                self._paths.state_file.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            payload = None
        if not isinstance(payload, dict):
            # Before the daemon's first publish there is nothing to show. 503
            # rather than an empty 200 so the client retries instead of
            # rendering a permanently blank companion.
            self._json(503, {"error": "state not published yet"})
            return
        self._json(200, api.public_state(payload, self._paths.sprite_dir))

    def _events(self) -> None:
        self._json(200, {"events": events.read(self._paths.events_file)})

    def _config(self) -> None:
        """The settings the browser is allowed to see and change.

        The engine's state payload deliberately carries no config, so without
        this the UI could only display hardcoded defaults -- which meant it lied
        about the current settings after any change, and the staleness banner
        had to assume a refresh interval rather than read one. Only the keys
        validate_config accepts are exposed; the engine's desktop-panel
        settings (floating pet, menu-bar items) describe UI that does not exist
        in a browser.
        """
        settings = config.load(self._paths.config_file)
        self._json(
            200,
            {key: settings[key] for key in sorted(api.WEB_CONFIG_KEYS) if key in settings},
        )

    def _sprite(self, route: str) -> None:
        try:
            name = unquote(route[len("/sprites/") :])
            resolved = api.sprite_file(name, self._paths.sprite_dir)
            if resolved is None:
                self._not_found()
                return
            body = resolved.read_bytes()
            ctype = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
            self._respond(200, body, ctype, SPRITE_CACHE)
        except Exception:
            self._not_found()

    def _static(self, route: str) -> None:
        """Serve web_root, with SPA fallback. The WHOLE body is guarded.

        Defect 3: ``/%00`` raises ValueError out of ``.resolve()`` and a
        5000-character segment raises OSError(ENAMETOOLONG) out of
        ``.is_file()`` — which pathlib does not ignore — so guarding only
        ``.resolve()`` returns an empty response for the latter.
        """
        try:
            root = Path(self._paths.web_root).resolve()
            relative = unquote(route).lstrip("/")
            candidate = (root / relative).resolve()

            # resolve() has followed every symlink, so an escape via ".." OR via
            # a link pointing outside the tree both land outside root here.
            if candidate != root and root not in candidate.parents:
                # 404, never the SPA shell: silently answering "../../etc/passwd"
                # with index.html makes a scan look like a working site and hides
                # it from anyone reading the access log.
                self._not_found()
                return

            if candidate.is_file():
                self._send_file(candidate, root)
                return

            # SPA routing: an extension-less miss is a client-side route.
            # A miss that names a file ("/app.js") is a real 404 — falling back
            # would hand the browser HTML labelled as JavaScript.
            if not _has_extension(relative):
                index = (root / "index.html").resolve()
                if index.is_file():
                    self._send_file(index, root)
                    return

            self._not_found()
        except Exception:
            self._not_found()

    def _send_file(self, path: Path, root: Path) -> None:
        body = path.read_bytes()
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self._respond(200, body, ctype, _cache_for(path, root))


def _cache_for(path: Path, root: Path) -> str:
    """Cache policy from the RESOLVED file, never from the request route.

    Defect 4: keying on ``route.startswith("/assets/")`` marks
    ``/assets/../index.html`` — the SPA shell — immutable for a year, so clients
    that requested it once are stranded on the old build across every deploy.
    Only a content-hashed bundle that really lives under assets/ qualifies;
    index.html, the manifest and the icons must revalidate.
    """
    try:
        relative = path.relative_to(root)
    except ValueError:  # pragma: no cover - caller has already contained it
        return REVALIDATE
    parts = relative.parts
    if parts[:1] == (HASHED_ASSET_DIR,) and _is_content_hashed(path.name):
        return IMMUTABLE
    return REVALIDATE


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, paths: Paths, boot_time: float):
        self.paths = paths
        #: When this process started. Injectable so a test can prove the probe
        #: turns red once the no-heartbeat boot grace expires without sleeping
        #: for five minutes.
        self.boot_time = boot_time
        super().__init__(address, handler)


def build_server(
    paths: Paths,
    host: str = "0.0.0.0",
    port: int = 8080,
    boot_time: float | None = None,
) -> ThreadingHTTPServer:
    """A bound, not-yet-serving HTTP server for `paths`.

    The caller runs ``serve_forever()``. Pass port 0 to let the OS choose one
    (tests) and read it back from ``server.server_address[1]``.
    """
    return _Server(
        (host, port),
        _Handler,
        paths,
        time.time() if boot_time is None else boot_time,
    )
