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


def test_healthz_200_with_fresh_heartbeat_long_after_boot(serve, paths):
    heartbeat.write(paths.heartbeat_file)
    port = serve(boot_time=time.time() - 86400)
    status, _headers, payload = get_json(port, "/healthz")
    assert status == 200
    assert payload["ok"] is True
    assert payload["heartbeat_age"] < 60


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

    monkeypatch.setattr(server.config, "set_value", boom)
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
