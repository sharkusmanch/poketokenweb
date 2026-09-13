"""The HTTP surface: routing, containment, cache policy and connection hygiene.

Two things these tests are shaped around, because both hid real defects:

*   **Keep-alive.** ``urllib.request.urlopen`` opens a fresh connection per
    call, so it structurally *cannot* observe a request/response desync. The
    rejection tests therefore drive one ``http.client.HTTPConnection`` through
    two sequential requests, the way a browser does.
*   **Raw paths.** ``..``, ``%00`` and 5000-character segments must reach the
    server verbatim, so requests go through ``http.client`` rather than any
    client that might normalise the path on the way out.
"""

from __future__ import annotations

import ast
import http.client
import json
import threading
import time
from pathlib import Path

import pytest

from poketokenbar import commands, config
from poketokenweb import api, heartbeat, paths as web_paths, server

# --- fixtures --------------------------------------------------------------


@pytest.fixture
def paths(tmp_path) -> web_paths.Paths:
    resolved = web_paths.resolve(
        {
            "POKETOKENWEB_DATA_DIR": str(tmp_path / "data"),
            "POKETOKENWEB_WEB_ROOT": str(tmp_path / "web"),
            "POKETOKENWEB_SPOOL_DIR": str(tmp_path / "spool"),
        }
    )
    resolved.ensure()
    # web_root ships inside the read-only image, so Paths.ensure() will not
    # create it (a missing mount must stay visible); the tests populate it.
    root = resolved.web_root
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><div id=app></div>", "utf-8")
    (root / "assets" / "app-a1b2c3d4.js").write_text("console.log(1)", "utf-8")
    (root / "assets" / "style.css").write_text("body{}", "utf-8")
    (root / "manifest.webmanifest").write_text("{}", "utf-8")
    return resolved


@pytest.fixture
def serve(paths):
    """Start a server on an ephemeral port; return its port number."""
    started: list = []

    def start(boot_time: float | None = None, use_paths=None) -> int:
        httpd = server.build_server(
            use_paths or paths, host="127.0.0.1", port=0, boot_time=boot_time
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        started.append((httpd, thread))
        return httpd.server_address[1]

    yield start

    for httpd, thread in started:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def connect(port: int) -> http.client.HTTPConnection:
    return http.client.HTTPConnection("127.0.0.1", port, timeout=10)


def request(port: int, method: str, path: str, body=None, headers=None):
    """One request on its own connection -> (status, headers, bytes)."""
    conn = connect(port)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        conn.close()


def get_json(port: int, path: str):
    status, headers, body = request(port, "GET", path)
    return status, headers, json.loads(body.decode("utf-8"))


def post_json(port: int, path: str, payload):
    status, _headers, body = request(
        port,
        "POST",
        path,
        body=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    return status, json.loads(body.decode("utf-8"))


def spooled(paths) -> list[Path]:
    return sorted(paths.spool_dir.glob("*.json"))


# --- architectural rule ----------------------------------------------------

FORBIDDEN_IMPORTS = {"companion_store", "save", "runner", "daemon"}


def test_web_thread_imports_no_state_mutating_engine_module():
    """The web thread's only write channels are the spool and config.json.

    Importing CompanionStore or save here would let two threads write
    companion.json; the rule is enforced mechanically rather than by review.
    """
    tree = ast.parse(Path(server.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.rsplit(".", 1)[-1] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.update(alias.name for alias in node.names)
            if node.module:
                imported.add(node.module.rsplit(".", 1)[-1])
    assert not (imported & FORBIDDEN_IMPORTS), sorted(imported & FORBIDDEN_IMPORTS)


# --- /healthz --------------------------------------------------------------


def test_healthz_200_during_boot_without_heartbeat(serve):
    """No heartbeat yet is startup, not death — but only for a bounded while."""
    port = serve(boot_time=time.time())
    status, _headers, payload = get_json(port, "/healthz")
    assert status == 200
    assert payload["ok"] is True
    assert payload["heartbeat_age"] is None


def test_healthz_503_once_boot_grace_expired_without_heartbeat(serve):
    """The failure that motivated heartbeat.healthy(): a daemon that died before
    its first poll used to leave the probe green forever."""
    port = serve(boot_time=time.time() - heartbeat.BOOT_GRACE_SECONDS - 60)
    status, _headers, payload = get_json(port, "/healthz")
    assert status == 503
    assert payload["ok"] is False
    assert payload["heartbeat_age"] is None


def test_healthz_200_with_a_fresh_successful_publish_long_after_boot(serve, paths):
    # `succeeded=True` is the point: an earlier version of this test wrote a
    # bare beat and asserted 200, which codified the defect that a daemon
    # failing every poll stayed green forever.
    heartbeat.write(paths.heartbeat_file, succeeded=True)
    port = serve(boot_time=time.time() - 86400)
    status, _headers, payload = get_json(port, "/healthz")
    assert status == 200
    assert payload["ok"] is True
    assert payload["heartbeat_age"] < 60
    assert payload["publish_age"] < 60


def test_healthz_503_with_stale_heartbeat(serve, paths):
    config.save(paths.config_file, {**config.DEFAULTS, "refresh_interval": 30})
    stale = heartbeat.stale_after(30) + 60
    heartbeat.write(paths.heartbeat_file, now=time.time() - stale)
    port = serve(boot_time=time.time() - 86400)
    status, _headers, payload = get_json(port, "/healthz")
    assert status == 503
    assert payload["ok"] is False
    assert payload["interval"] == 30


def test_healthz_reads_the_configured_interval(serve, paths):
    config.save(paths.config_file, {**config.DEFAULTS, "refresh_interval": 900})
    port = serve()
    _status, _headers, payload = get_json(port, "/healthz")
    assert payload["interval"] == 900


def test_healthz_is_never_cached(serve):
    port = serve()
    _status, headers, _payload = get_json(port, "/healthz")
    assert headers["Cache-Control"] == "no-store"


# --- /api/state ------------------------------------------------------------


def test_state_503_before_first_publish(serve):
    port = serve()
    status, _headers, payload = get_json(port, "/api/state")
    assert status == 503
    assert "error" in payload


def test_state_503_when_state_file_is_corrupt(serve, paths):
    paths.state_file.write_text("{not json", encoding="utf-8")
    port = serve()
    status, _headers, _payload = get_json(port, "/api/state")
    assert status == 503


def test_state_serves_public_view(serve, paths):
    paths.state_file.write_text(
        json.dumps(
            {
                "companion": {"sprite_path": "/data/cache/sprites/25-0.gif"},
                "errors": ["cannot read /home/u/.claude/.credentials.json"],
            }
        ),
        encoding="utf-8",
    )
    port = serve()
    status, headers, payload = get_json(port, "/api/state")
    assert status == 200
    assert payload["companion"]["sprite_path"] == "/sprites/25-0.gif"
    assert payload["errors"] == ["cannot read <path>"]
    assert headers["Cache-Control"] == "no-store"


# --- /api/events -----------------------------------------------------------


def test_events_empty_when_never_written(serve):
    port = serve()
    status, _headers, payload = get_json(port, "/api/events")
    assert status == 200
    assert payload["events"] == []


def test_events_newest_first(serve, paths):
    from poketokenweb import events as events_mod

    events_mod.append(paths.events_file, {"kind": "hatch", "title": "old"}, now=1.0)
    events_mod.append(paths.events_file, {"kind": "hatch", "title": "new"}, now=2.0)
    port = serve()
    _status, _headers, payload = get_json(port, "/api/events")
    assert [entry["title"] for entry in payload["events"]] == ["new", "old"]


# --- POST /api/command -----------------------------------------------------


def test_command_accepted_and_spooled(serve, paths):
    port = serve()
    status, payload = post_json(port, "/api/command", {"name": "refresh"})
    assert status == 202
    assert payload["name"] == "refresh"
    assert commands.drain(paths.spool_dir) == [{"name": "refresh", "args": {}}]


def test_buy_command_carries_its_key(serve, paths):
    port = serve()
    status, _payload = post_json(
        port, "/api/command", {"name": "buy", "args": {"key": "rareCandy"}}
    )
    assert status == 202
    assert commands.drain(paths.spool_dir) == [
        {"name": "buy", "args": {"key": "rareCandy"}}
    ]


def test_rejected_command_leaves_the_spool_empty(serve, paths):
    """A 400 must never half-happen: nothing may reach the daemon."""
    port = serve()
    status, payload = post_json(port, "/api/command", {"name": "rm -rf"})
    assert status == 400
    assert "error" in payload
    assert spooled(paths) == []


def test_unhashable_command_argument_is_a_400_not_a_500(serve, paths):
    """{"key": {}} is unhashable; a naive `in` membership test raises TypeError."""
    port = serve()
    status, _payload = post_json(
        port, "/api/command", {"name": "buy", "args": {"key": {}}}
    )
    assert status == 400
    assert spooled(paths) == []


def test_unusable_command_is_rejected(serve, paths):
    """shinyCharm is passive; use_item() can only fail on it."""
    port = serve()
    status, _payload = post_json(
        port, "/api/command", {"name": "use", "args": {"key": "shinyCharm"}}
    )
    assert status == 400
    assert spooled(paths) == []


def test_post_to_unknown_route_is_404(serve):
    port = serve()
    status, _payload = post_json(port, "/api/nope", {"name": "refresh"})
    assert status == 404


# --- POST /api/config ------------------------------------------------------


def test_config_change_persists_and_asks_the_daemon_to_reload(serve, paths):
    port = serve()
    status, payload = post_json(
        port, "/api/config", {"key": "refresh_interval", "value": 300}
    )
    assert status == 202
    assert payload["key"] == "refresh_interval"
    assert config.load(paths.config_file)["refresh_interval"] == 300
    # Writing the file is not enough: the daemon holds settings in memory.
    assert commands.drain(paths.spool_dir) == [
        {"name": "reload_config", "args": {}}
    ]


def test_out_of_range_config_changes_nothing(serve, paths):
    port = serve()
    status, _payload = post_json(
        port, "/api/config", {"key": "refresh_interval", "value": 1}
    )
    assert status == 400
    assert config.load(paths.config_file)["refresh_interval"] == 120
    assert spooled(paths) == []


def test_non_web_config_key_is_rejected(serve, paths):
    """floating_pet_enabled configures a desktop widget that does not exist here."""
    port = serve()
    status, _payload = post_json(
        port, "/api/config", {"key": "floating_pet_enabled", "value": True}
    )
    assert status == 400
    assert spooled(paths) == []


# --- defect 1: keep-alive desync -------------------------------------------


def test_rejected_post_does_not_desync_the_keepalive_connection(serve):
    """Two requests, ONE connection — the browser case.

    Rejecting the POST without draining its body leaves ``{"name":"refresh"}``
    in the socket, and the next request line is parsed from it:
    ``501 Unsupported method ('{"name":"refresh"}GET')``. urlopen cannot see
    this because it never reuses a connection.
    """
    port = serve()
    conn = connect(port)
    try:
        conn.request(
            "POST",
            "/api/command",
            body=b'{"name":"refresh"}',
            headers={"Content-Type": "text/plain"},
        )
        first = conn.getresponse()
        first.read()
        assert first.status == 415

        conn.request("GET", "/healthz")
        second = conn.getresponse()
        raw = second.read()
        assert second.status == 200, f"desync: {second.status} {second.reason}"
        assert "ok" in json.loads(raw.decode("utf-8"))
    finally:
        conn.close()


def test_malformed_json_body_leaves_the_connection_usable(serve):
    port = serve()
    conn = connect(port)
    try:
        conn.request(
            "POST",
            "/api/command",
            body=b"{ not json",
            headers={"Content-Type": "application/json"},
        )
        first = conn.getresponse()
        first.read()
        assert first.status == 400

        conn.request("GET", "/healthz")
        second = conn.getresponse()
        second.read()
        assert second.status == 200, f"desync: {second.status} {second.reason}"
    finally:
        conn.close()


def test_oversized_body_is_rejected_without_reaching_the_spool(serve, paths):
    """The body is refused on its Content-Length, before a byte is buffered."""
    port = serve()
    conn = connect(port)
    try:
        conn.putrequest("POST", "/api/command")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", str(server.MAX_BODY_BYTES + 1))
        conn.endheaders()  # deliberately send no body at all
        response = conn.getresponse()
        response.read()
        assert response.status == 413
    finally:
        conn.close()
    assert spooled(paths) == []


def test_chunked_body_is_refused(serve):
    port = serve()
    conn = connect(port)
    try:
        conn.putrequest("POST", "/api/command", skip_accept_encoding=True)
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Transfer-Encoding", "chunked")
        conn.endheaders()
        response = conn.getresponse()
        response.read()
        assert response.status == 411
    finally:
        conn.close()


# --- defect 2: unhandled exception types -----------------------------------


def test_spool_write_failure_returns_500_json(serve, monkeypatch):
    """An OSError writing the spool must not drop the connection and dump a
    traceback full of server paths."""

    def boom(*_args, **_kwargs):
        raise OSError(28, "No space left on device", "/data/spool/x.json")

    monkeypatch.setattr(server.commands, "enqueue", boom)
    port = serve()
    status, payload = post_json(port, "/api/command", {"name": "refresh"})
    assert status == 500
    assert payload == {"error": "internal error"}


def test_config_write_failure_returns_500_json(serve, monkeypatch):
    def boom(*_args, **_kwargs):
        raise PermissionError(13, "Permission denied", "/data/config/config.json")

    # Config writes no longer go through the engine's set_value: it is an
    # unsynchronised read-modify-write through a fixed temp name.
    monkeypatch.setattr(server, "_write_setting", boom)
    port = serve()
    status, payload = post_json(
        port, "/api/config", {"key": "language", "value": "ko"}
    )
    assert status == 500
    assert payload == {"error": "internal error"}


# --- /sprites --------------------------------------------------------------


def test_sprite_served_with_a_long_cache_header(serve, paths):
    (paths.sprite_dir / "25-0.gif").write_bytes(b"GIF89a")
    port = serve()
    status, headers, body = request(port, "GET", "/sprites/25-0.gif")
    assert status == 200
    assert body == b"GIF89a"
    assert headers["Content-Type"] == "image/gif"
    assert "max-age=2592000" in headers["Cache-Control"]


def test_missing_sprite_is_404(serve):
    port = serve()
    status, _headers, _body = request(port, "GET", "/sprites/999-0.gif")
    assert status == 404


def test_sprite_traversal_is_404(serve, tmp_path):
    (tmp_path / "secret.txt").write_text("token", encoding="utf-8")
    port = serve()
    for route in (
        "/sprites/..%2F..%2Fsecret.txt",
        "/sprites/../../secret.txt",
        "/sprites/%2e%2e%2fsecret.txt",
        "/sprites//etc/passwd",
    ):
        status, _headers, body = request(port, "GET", route)
        assert status == 404, route
        assert b"token" not in body, route


def test_sprite_symlink_escape_is_404(serve, paths, tmp_path):
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"secret")
    (paths.sprite_dir / "link.png").symlink_to(outside)
    port = serve()
    status, _headers, body = request(port, "GET", "/sprites/link.png")
    assert status == 404
    assert b"secret" not in body


# --- static files ----------------------------------------------------------


def test_index_is_served_at_root(serve):
    port = serve()
    status, headers, body = request(port, "GET", "/")
    assert status == 200
    assert b"id=app" in body
    assert headers["Content-Type"] == "text/html"


def test_hashed_asset_is_immutable(serve):
    port = serve()
    status, headers, _body = request(port, "GET", "/assets/app-a1b2c3d4.js")
    assert status == 200
    assert headers["Cache-Control"] == server.IMMUTABLE


def test_index_html_is_not_immutable(serve):
    """A build ships a new index.html at the same URL every time."""
    port = serve()
    _status, headers, _body = request(port, "GET", "/index.html")
    assert headers["Cache-Control"] == server.REVALIDATE


def test_manifest_is_not_immutable(serve):
    port = serve()
    _status, headers, _body = request(port, "GET", "/manifest.webmanifest")
    assert headers["Cache-Control"] == server.REVALIDATE


def test_unhashed_file_under_assets_is_not_immutable(serve):
    port = serve()
    _status, headers, _body = request(port, "GET", "/assets/style.css")
    assert headers["Cache-Control"] == server.REVALIDATE


def test_assets_traversal_to_index_is_not_immutable(serve):
    """Defect 4: choosing the policy from the REQUEST route marks the SPA shell
    immutable for a year and strands every client that asked for it this way."""
    port = serve()
    status, headers, body = request(port, "GET", "/assets/../index.html")
    assert status == 200
    assert b"id=app" in body
    assert headers["Cache-Control"] == server.REVALIDATE


def test_spa_fallback_for_an_extensionless_route(serve):
    port = serve()
    status, headers, body = request(port, "GET", "/bag/items")
    assert status == 200
    assert b"id=app" in body
    assert headers["Cache-Control"] == server.REVALIDATE


def test_missing_file_with_an_extension_is_404_not_the_shell(serve):
    """Answering /app.js with HTML hands the browser a script it cannot parse."""
    port = serve()
    status, _headers, body = request(port, "GET", "/nope.js")
    assert status == 404
    assert b"id=app" not in body


def test_static_traversal_is_404_not_the_spa_shell(serve, tmp_path):
    """Silently serving index.html for ../ makes a scan look like a working
    site and hides it from anyone reading the access log."""
    (tmp_path / "secret.txt").write_text("token", encoding="utf-8")
    port = serve()
    for route in (
        "/../secret.txt",
        "/../../etc/passwd",
        "/assets/../../secret.txt",
        "/%2e%2e/secret.txt",
    ):
        status, _headers, body = request(port, "GET", route)
        assert status == 404, route
        assert b"token" not in body, route
        assert b"id=app" not in body, route


def test_static_symlink_escape_is_404(serve, paths, tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("token", encoding="utf-8")
    (paths.web_root / "escape.txt").symlink_to(outside)
    port = serve()
    status, _headers, body = request(port, "GET", "/escape.txt")
    assert status == 404
    assert b"token" not in body


# --- defect 3: _static must guard its whole body ---------------------------


def test_nul_byte_path_is_404_with_a_real_response(serve):
    """`Path("...\\0").resolve()` raises ValueError: embedded null character."""
    port = serve()
    status, _headers, body = request(port, "GET", "/%00")
    assert status == 404
    assert json.loads(body.decode("utf-8"))["error"] == "not found"


def test_nul_byte_inside_path_is_404_with_a_real_response(serve):
    port = serve()
    status, _headers, body = request(port, "GET", "/a%00b")
    assert status == 404
    assert json.loads(body.decode("utf-8"))["error"] == "not found"


def test_very_long_path_is_404_with_a_real_response(serve):
    """5000 chars survives resolve() and then raises OSError(ENAMETOOLONG) out
    of Path.is_file(), which pathlib does NOT ignore — outside any try that
    wraps only .resolve()."""
    port = serve()
    status, _headers, body = request(port, "GET", "/" + "a" * 5000)
    assert status == 404
    assert json.loads(body.decode("utf-8"))["error"] == "not found"


def test_very_long_sprite_name_is_404(serve):
    port = serve()
    status, _headers, _body = request(port, "GET", "/sprites/" + "a" * 5000 + ".gif")
    assert status == 404


# --- pure helpers ----------------------------------------------------------


@pytest.mark.parametrize(
    "name,hashed",
    [
        ("app-a1b2c3d4.js", True),
        ("index-DdXtT1nq.js", True),
        ("index.html", False),
        ("style.css", False),
        ("manifest.webmanifest", False),
        ("favicon.ico", False),
        ("apple-touch-icon.png", False),
    ],
)
def test_is_content_hashed(name, hashed):
    assert server._is_content_hashed(name) is hashed


def test_cache_for_uses_the_resolved_path(tmp_path):
    root = tmp_path
    (root / "assets").mkdir()
    assert server._cache_for(root / "assets" / "a-1b2c3d4e.js", root) == server.IMMUTABLE
    assert server._cache_for(root / "index.html", root) == server.REVALIDATE
    # The shell reached "through" assets/ is still the shell.
    assert server._cache_for((root / "assets" / ".." / "index.html").resolve(), root) == (
        server.REVALIDATE
    )


# --- GET /api/config -------------------------------------------------------
# The state payload carries no config, so the UI reads its current settings
# here. Without it Settings can only render hardcoded defaults (lying after any
# change) and the staleness banner must guess the refresh interval.

def test_config_exposes_only_the_web_settable_keys(serve):
    port = serve()
    status, _headers, body = get_json(port, "/api/config")
    assert status == 200
    assert set(body) == set(api.WEB_CONFIG_KEYS)


def test_config_excludes_desktop_panel_settings(serve):
    port = serve()
    _status, _headers, body = get_json(port, "/api/config")
    for key in ("floating_pet_enabled", "show_tokens_in_menu", "limit_notifications"):
        assert key not in body


def test_config_returns_engine_defaults_before_any_file_exists(serve, paths):
    port = serve()
    assert not paths.config_file.exists()
    _status, _headers, body = get_json(port, "/api/config")
    assert body["refresh_interval"] == 120
    assert body["language"] == "en"


def test_config_reflects_a_written_change(serve):
    port = serve()
    assert get_json(port, "/api/config")[2]["refresh_interval"] == 120
    assert post_json(port, "/api/config", {"key": "refresh_interval", "value": 300})[0] == 202
    assert get_json(port, "/api/config")[2]["refresh_interval"] == 300


def test_config_is_never_cached(serve):
    port = serve()
    _status, headers, _body = get_json(port, "/api/config")
    assert "no-store" in headers.get("Cache-Control", "")


# --- HEAD ------------------------------------------------------------------
# BaseHTTPRequestHandler answers 501 unless do_HEAD exists, which breaks uptime
# checkers and proxy probes. Found by curl -I against a real build, not by any
# unit test.

def test_head_on_the_spa_shell_matches_get_headers_without_a_body(serve, paths):
    port = serve()
    (paths.web_root / "index.html").write_text("<!doctype html><title>x</title>")
    get_status, get_headers, get_body = request(port, "GET", "/")
    head_status, head_headers, head_body = request(port, "HEAD", "/")
    assert head_status == get_status == 200
    assert head_body == b""
    # Content-Length must still describe the entity that GET would return.
    assert head_headers["Content-Length"] == get_headers["Content-Length"]
    assert head_headers["Content-Type"] == get_headers["Content-Type"]


def test_head_on_healthz(serve):
    port = serve()
    status, headers, body = request(port, "HEAD", "/healthz")
    assert status == 200
    assert body == b""
    assert int(headers["Content-Length"]) > 0


def test_head_sends_no_body_on_the_wire(serve, paths):
    """Read the raw socket: http.client discards HEAD bodies by protocol.

    An assertion on `response.read()` therefore passes even when the server
    really does write a body, because the client never reads it. Only the raw
    bytes reveal the desynchronisation that body would cause.
    """
    import socket as _socket

    port = serve()
    (paths.web_root / "index.html").write_text("<!doctype html><title>x</title>")
    sock = _socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        sock.sendall(b"HEAD / HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        raw = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            raw += chunk
    finally:
        sock.close()

    head, _, body = raw.partition(b"\r\n\r\n")
    assert b"200" in head.split(b"\r\n")[0]
    assert b"Content-Length:" in head
    assert body == b"", f"HEAD wrote {len(body)} bytes of body: {body[:80]!r}"


def test_head_does_not_desync_a_keepalive_connection(serve, paths):
    """A body written for HEAD is parsed as the next request."""
    port = serve()
    (paths.web_root / "index.html").write_text("<!doctype html><title>x</title>")
    conn = connect(port)
    try:
        conn.request("HEAD", "/")
        first = conn.getresponse()
        first.read()
        assert first.status == 200
        conn.request("GET", "/healthz")
        second = conn.getresponse()
        payload = json.loads(second.read())
        assert second.status == 200 and payload["ok"] is True
    finally:
        conn.close()


# --- content-hash detection ------------------------------------------------
# Vite hashes are base64url-ish and often contain no digit whatsoever, so an
# any(isdigit) test rejects a real hashed bundle and serves it no-cache forever.

@pytest.mark.parametrize("name", [
    "index-BL0uELsO.js",     # observed in a real build
    "index-DmWNSQuv.css",    # observed; contains NO digit
    "index-4f3a9b2c.js",
    "chunk-a1b2c3d4.js",
])
def test_real_build_asset_names_are_recognised_as_hashed(name):
    assert server._is_content_hashed(name) is True


@pytest.mark.parametrize("name", [
    "index.html",
    "manifest.webmanifest",
    "favicon.png",
    "bundle.js",             # no separator: not a hashed name
    "vendor-lodash.js",      # a word, not a hash
])
def test_unhashed_names_are_not_immutable(name):
    assert server._is_content_hashed(name) is False


def test_a_hashed_css_asset_is_served_immutable(serve, paths):
    port = serve()
    assets = paths.web_root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "index-DmWNSQuv.css").write_text("body{}")
    _status, headers, _body = request(port, "GET", "/assets/index-DmWNSQuv.css")
    assert "immutable" in headers["Cache-Control"]


# --- concurrent settings writes --------------------------------------------
# ThreadingHTTPServer runs a thread per request, and the Settings screen commits
# a number field on blur -- so clicking the language select fires two POSTs at
# once. The engine's config.set_value is an unsynchronised read-modify-write
# through a FIXED temp name. Measured before the fix: 80 rounds of two
# concurrent writes gave 22 corrupt files and 57 lost updates, and a corrupt
# file reads back as DEFAULTS, silently discarding every setting.

def test_our_writer_matches_the_engines_coercion_for_every_settable_key(tmp_path):
    from poketokenbar import config as engine_config
    from poketokenweb.server import _write_setting

    samples = {
        "refresh_interval": "300",
        "warn_threshold": "70",
        "crit_threshold": "90",
        "limit_display_mode": "weekly",
        "language": "ja",
    }
    for key, value in samples.items():
        ours = tmp_path / f"ours-{key}.json"
        theirs = tmp_path / f"theirs-{key}.json"
        _write_setting(ours, key, value)
        engine_config.set_value(theirs, key, value)
        assert json.loads(ours.read_text())[key] == json.loads(theirs.read_text())[key]
        assert type(json.loads(ours.read_text())[key]) is type(
            json.loads(theirs.read_text())[key]
        )


def test_concurrent_setting_writes_never_corrupt_or_lose(serve):
    port = serve()
    changes = [
        ("language", "ko"),
        ("refresh_interval", 3600),
        ("limit_display_mode", "weekly"),
        ("warn_threshold", 70),
    ]
    errors: list = []

    def push(pair):
        key, value = pair
        try:
            status, _body = post_json(port, "/api/config", {"key": key, "value": value})
            if status != 202:
                errors.append((key, status))
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    for _round in range(15):
        threads = [threading.Thread(target=push, args=(c,)) for c in changes]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    assert not errors, errors
    # Every change must be present: a lost update or a torn file that reads back
    # as DEFAULTS would drop one.
    _status, _headers, seen = get_json(port, "/api/config")
    assert seen["language"] == "ko"
    assert seen["refresh_interval"] == 3600
    assert seen["limit_display_mode"] == "weekly"
    assert seen["warn_threshold"] == 70


def test_no_temp_files_are_left_behind_by_concurrent_writes(serve, paths):
    port = serve()
    threads = [
        threading.Thread(
            target=post_json,
            args=(port, "/api/config", {"key": "warn_threshold", "value": v}),
        )
        for v in (60, 65, 70, 75)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert list(paths.config_file.parent.glob("*.tmp")) == []


# --- /healthz reports publishing, not merely beating ------------------------

def test_healthz_503_when_the_daemon_beats_but_never_publishes(serve, paths):
    """The regression: every poll raising kept the probe green forever."""
    import json as _json

    port = serve(boot_time=time.time() - (heartbeat.BOOT_GRACE_SECONDS + 60))
    # Beating right now, but no successful publish has ever been recorded.
    paths.heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
    paths.heartbeat_file.write_text(_json.dumps({"beat": time.time(), "ok_at": None}))

    status, _headers, body = get_json(port, "/healthz")
    assert status == 503, body
    assert body["ok"] is False
    assert body["publish_age"] is None


def test_healthz_503_when_publishing_stopped_though_polls_continue(serve, paths):
    import json as _json

    port = serve(boot_time=time.time() - 10_000)
    now = time.time()
    paths.heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
    paths.heartbeat_file.write_text(
        _json.dumps({"beat": now, "ok_at": now - (heartbeat.stale_after(120) + 60)})
    )

    status, _headers, body = get_json(port, "/healthz")
    assert status == 503, body


def test_healthz_200_with_a_recent_publish(serve, paths):
    import json as _json

    port = serve(boot_time=time.time() - 10_000)
    now = time.time()
    paths.heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
    paths.heartbeat_file.write_text(_json.dumps({"beat": now, "ok_at": now - 5}))

    status, _headers, body = get_json(port, "/healthz")
    assert status == 200, body
    assert body["publish_age"] < 60


# --- spool bounding, error logging, nested JSON -----------------------------
# The daemon drains once per refresh interval (up to an hour), so an unbounded
# queue lets any client that can reach the port fill the disk: ~300 accepted
# commands/second was measured from a single host.

def test_a_saturated_spool_is_429_not_unbounded_growth(serve, paths):
    port = serve()
    accepted = 0
    saturated = 0
    for _ in range(server.MAX_PENDING_COMMANDS + 40):
        status, _body = post_json(port, "/api/command", {"name": "refresh"})
        if status == 202:
            accepted += 1
        elif status == 429:
            saturated += 1
    assert saturated > 0, "spool accepted every command; it is unbounded"
    assert accepted <= server.MAX_PENDING_COMMANDS
    assert len(list(paths.spool_dir.glob("*.json"))) <= server.MAX_PENDING_COMMANDS


def test_the_spool_accepts_again_once_drained(serve, paths):
    port = serve()
    for _ in range(server.MAX_PENDING_COMMANDS + 5):
        post_json(port, "/api/command", {"name": "refresh"})
    for spooled in paths.spool_dir.glob("*.json"):
        spooled.unlink()
    status, _body = post_json(port, "/api/command", {"name": "refresh"})
    assert status == 202


def test_deeply_nested_json_is_400_not_500(serve):
    # Parses fine, then blows the stack inside validation. Bad input, not a
    # server fault -- and it must not be reported as one.
    depth = 30_000
    body = '{"name": ' + "[" * depth + "]" * depth + "}"
    status, _headers, raw = request(
        serve(), "POST", "/api/command",
        body=body.encode(), headers={"Content-Type": "application/json"},
    )
    assert status == 400


def test_a_500_is_logged(serve, monkeypatch, capfd):
    def boom(*_args, **_kwargs):
        raise PermissionError(13, "Permission denied", "/data/config/config.json")

    monkeypatch.setattr(server, "_write_setting", boom)
    port = serve()
    status, _payload = post_json(port, "/api/config", {"key": "language", "value": "ko"})
    assert status == 500
    # An unlogged 500 leaves no trace in the container's only log sink.
    err = capfd.readouterr().err
    assert "500" in err and "PermissionError" in err
    # The traceback names server paths and must not be printed.
    assert "/data/config/config.json" not in err


# --- GET /api/pokemon/<id> (#264) -------------------------------------------


class TestPokemonDetail:
    """The detail route. Species data needs the network, which these tests do
    not have, so the interesting assertions are about validation and the
    unavailable path -- both of which are the reachable states in a container
    with restricted egress."""

    def test_a_non_numeric_id_never_reaches_the_network(self, serve):
        port = serve()
        for bad in ("abc", "..", "%2e%2e%2f", "1.5", "-3", ""):
            status, _, _ = request(port, "GET", f"/api/pokemon/{bad}")
            assert status == 404, bad

    def test_an_absurd_id_is_rejected_before_a_fetch(self, serve):
        port = serve()
        status, _, _ = request(port, "GET", "/api/pokemon/999999999999")
        assert status == 404

    def test_zero_is_not_a_species(self, serve):
        port = serve()
        status, _, _ = request(port, "GET", "/api/pokemon/0")
        assert status == 404

    def test_unavailable_species_data_is_a_404_not_a_500(self, serve, monkeypatch):
        """PokeAPI being unreachable is an expected state for an offline
        deployment, not a bug in this server."""
        monkeypatch.setattr(server.detail, "payload", lambda *a, **k: None)
        port = serve()
        status, _, body = request(port, "GET", "/api/pokemon/25")
        assert status == 404
        assert json.loads(body)["error"] == "details unavailable"

    def test_a_detail_page_is_served_as_json(self, serve, monkeypatch):
        monkeypatch.setattr(
            server.detail,
            "payload",
            lambda *a, **k: {"species_id": 25, "name": "Pikachu", "sprite_path": ""},
        )
        port = serve()
        status, headers, body = request(port, "GET", "/api/pokemon/25")
        assert status == 200
        assert headers["Content-Type"].startswith("application/json")
        assert json.loads(body)["name"] == "Pikachu"

    def test_sprite_paths_are_rewritten_like_every_other_payload(
        self, serve, monkeypatch
    ):
        monkeypatch.setattr(
            server.detail,
            "payload",
            lambda *a, **k: {
                "species_id": 25,
                "sprite_path": "/data/cache/sprites/25-s.png",
            },
        )
        port = serve()
        _, _, body = request(port, "GET", "/api/pokemon/25")
        assert json.loads(body)["sprite_path"] == "/sprites/25-s.png"

    def test_a_crash_in_the_detail_builder_does_not_take_the_app_down(
        self, serve, monkeypatch
    ):
        def explode(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(server.detail, "payload", explode)
        port = serve()
        status, _, _ = request(port, "GET", "/api/pokemon/25")
        assert status == 404
        # The server is still answering.
        assert request(port, "GET", "/healthz")[0] in (200, 503)
