"""Path resolution for the containerised web app.

The container mounts HOME read-only (it carries the user's Claude/Codex logs),
so these tests pin two things: no writable path may be derived from HOME, and
the paths handed to the vendored engine classes must be the same ones the web
server reads back.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

import pytest

from poketokenbar.pokeapi import PokeAPI
from poketokenbar.sprites import SpriteStore
from poketokenweb import paths as web_paths

# Everything the app writes that must live on the /data volume. spool_dir and
# web_root are deliberately excluded: the env contract puts the spool on tmpfs
# and the web root inside the (read-only) image.
DATA_SCOPED = (
    "state_file",
    "config_file",
    "save_file",
    "events_file",
    "heartbeat_file",
    "cache_dir",
    "sprite_dir",
    "scan_db",
)

ALL_FIELDS = DATA_SCOPED + ("spool_dir", "web_root")


@pytest.fixture
def env(tmp_path) -> dict[str, str]:
    return {
        "POKETOKENWEB_DATA_DIR": str(tmp_path / "data"),
        "POKETOKENWEB_WEB_ROOT": str(tmp_path / "web"),
        "POKETOKENWEB_SPOOL_DIR": str(tmp_path / "spool"),
    }


@pytest.fixture
def set_tz(monkeypatch):
    """Change the *process* timezone, and put it back afterwards.

    monkeypatch.undo() alone is not enough: libc caches the zone until
    time.tzset() runs again, so a leaked TZ would poison later tests.
    """

    def apply(value: str) -> None:
        monkeypatch.setenv("TZ", value)
        time.tzset()

    yield apply
    monkeypatch.undo()
    time.tzset()


def observed_timezone():
    """What Python actually resolves right now — the thing the code must key on."""
    return (tuple(time.tzname), datetime.now().astimezone().utcoffset())


# --- 1. layout ------------------------------------------------------------


def test_writable_paths_live_under_the_data_dir(env, tmp_path):
    p = web_paths.resolve(env)
    data = tmp_path / "data"
    for field in DATA_SCOPED:
        value = getattr(p, field)
        assert isinstance(value, Path), f"{field} must be a Path, got {type(value)}"
        assert value.is_absolute(), f"{field} is not absolute: {value}"
        assert value.is_relative_to(data), f"{field} escapes the data dir: {value}"
    # The two intentional exceptions come from their own env vars.
    assert p.spool_dir == tmp_path / "spool"
    assert p.web_root == tmp_path / "web"


def test_no_path_is_derived_from_home(env):
    """HOME is a read-only mount; a Path.home() fallback would fail at runtime."""
    p = web_paths.resolve(env)
    home = Path.home()
    for field in ALL_FIELDS:
        value = getattr(p, field)
        assert not value.is_relative_to(home), f"{field} sits under HOME: {value}"


def test_paths_is_frozen_with_the_agreed_field_order():
    import dataclasses

    assert [f.name for f in dataclasses.fields(web_paths.Paths)] == list(ALL_FIELDS)
    p = web_paths.resolve({})
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.cache_dir = Path("/elsewhere")


# --- 2. ensure() ----------------------------------------------------------


def test_ensure_creates_every_writable_directory(env, tmp_path):
    p = web_paths.resolve(env)
    p.ensure()
    for field in ("cache_dir", "sprite_dir", "spool_dir"):
        assert getattr(p, field).is_dir(), f"ensure() did not create {field}"
    for field in (
        "state_file",
        "config_file",
        "save_file",
        "events_file",
        "heartbeat_file",
        "scan_db",
    ):
        parent = getattr(p, field).parent
        assert parent.is_dir(), f"ensure() did not create the parent of {field}"
    # The web root ships inside the read-only image; creating it would either
    # fail or mask a bad mount.
    assert not p.web_root.exists()


def test_ensure_is_idempotent(env):
    p = web_paths.resolve(env)
    p.ensure()
    p.ensure()
    assert p.sprite_dir.is_dir()


# --- 3./4. the vendored engine classes must agree with us -----------------


def test_sprite_store_writes_where_we_serve_from(env):
    """SpriteStore hardcodes <cache>/sprites; a mismatch would have the server
    serving an empty folder while the daemon fills another."""
    p = web_paths.resolve(env)
    p.ensure()
    assert SpriteStore(cache_dir=p.cache_dir).dir == p.sprite_dir


def test_pokeapi_caches_inside_our_cache_dir(env, tmp_path):
    p = web_paths.resolve(env)
    p.ensure()
    api = PokeAPI(cache_dir=p.cache_dir)
    assert api.cache_dir == p.cache_dir
    assert api.cache_dir.is_dir()
    assert api.cache_dir.is_relative_to(tmp_path / "data")
    # The sprite dir is served over HTTP; the API's JSON cache must not be it.
    assert p.sprite_dir != api.cache_dir
    assert p.sprite_dir.is_relative_to(api.cache_dir)


def test_engine_classes_would_use_home_without_an_explicit_cache_dir():
    """Pins why every caller must pass cache_dir: the default is HOME-based."""
    import inspect

    for module in (SpriteStore, PokeAPI):
        source = inspect.getsource(module.__init__)
        assert "Path.home()" in source


# --- 5. defaults ----------------------------------------------------------


def test_defaults_when_env_is_empty():
    p = web_paths.resolve({})
    assert p.state_file == Path("/data/state/state.json")
    assert p.events_file == Path("/data/state/events.json")
    assert p.heartbeat_file == Path("/data/state/heartbeat")
    assert p.config_file == Path("/data/config/config.json")
    assert p.save_file == Path("/data/save/companion.json")
    assert p.cache_dir == Path("/data/cache")
    assert p.sprite_dir == Path("/data/cache/sprites")
    assert p.scan_db.parent == Path("/data/cache")
    assert p.scan_db.name.startswith("scan-") and p.scan_db.name.endswith(".db")
    assert p.spool_dir == Path("/tmp/poketokenbar/commands")
    assert p.web_root == Path("/app/web")


def test_blank_env_values_fall_back_to_defaults():
    """k8s renders an unset var as "" rather than omitting it."""
    p = web_paths.resolve(
        {
            "POKETOKENWEB_DATA_DIR": "",
            "POKETOKENWEB_WEB_ROOT": "",
            "POKETOKENWEB_SPOOL_DIR": "",
        }
    )
    assert p.state_file == Path("/data/state/state.json")
    assert p.web_root == Path("/app/web")
    assert p.spool_dir == Path("/tmp/poketokenbar/commands")


# --- 6. the scan DB name must follow the OBSERVED timezone ----------------


@pytest.mark.parametrize(
    "first,second",
    [
        ("America/Los_Angeles", "Asia/Seoul"),  # named zones, need tzdata
        ("ABC5", "XYZ-9"),  # POSIX form, works without tzdata
        # Abbreviation differs only by sign: naive slugifying folds "+07" and
        # "-07" onto the same token, so the offsets must carry the sign safely.
        ("<+07>-7", "<-07>7"),
    ],
)
def test_different_effective_timezones_give_different_scan_db_names(
    env, set_tz, first, second
):
    set_tz(first)
    seen_first = observed_timezone()
    name_first = web_paths.resolve(env).scan_db.name

    set_tz(second)
    seen_second = observed_timezone()
    name_second = web_paths.resolve(env).scan_db.name

    # Only meaningful if the two really do bucket days differently here.
    if seen_first == seen_second:
        pytest.skip(f"{first} and {second} resolve identically: {seen_first}")
    assert name_first != name_second, (
        f"{seen_first} and {seen_second} bucket local_day differently but share "
        f"the scan DB name {name_first}"
    )


def test_same_effective_timezone_is_stable(env, set_tz):
    set_tz("Asia/Seoul")
    first = web_paths.resolve(env).scan_db.name
    set_tz("Europe/Berlin")
    set_tz("Asia/Seoul")
    assert web_paths.resolve(env).scan_db.name == first


def test_equivalent_tz_spellings_share_one_scan_db(env, set_tz):
    """The killer for a filename keyed on the raw TZ string: these two spellings
    are the same effective zone, so they must not split the cache."""
    set_tz("UTC")
    seen_utc = observed_timezone()
    name_utc = web_paths.resolve(env).scan_db.name
    set_tz("UTC0")
    if observed_timezone() != seen_utc:
        pytest.skip("UTC and UTC0 do not resolve identically on this platform")
    assert web_paths.resolve(env).scan_db.name == name_utc


def test_scan_db_name_reflects_the_resolved_zone_abbreviation(env, set_tz):
    """Derived from the same observable the code must use — no guessed slug."""
    set_tz("Asia/Seoul")
    expected_fragment = time.tzname[0].lower()  # 'kst' with tzdata, 'utc' without
    name = web_paths.resolve(env).scan_db.name
    assert expected_fragment in name, f"{expected_fragment!r} not in {name!r}"


def test_scan_db_name_is_filesystem_safe(env, set_tz):
    import re

    # Kathmandu and Marquesas have no letter abbreviation: tzname is "+0545" /
    # "-0930", so the raw zone data really does contain path-hostile characters.
    for tz in (
        "America/Los_Angeles",
        "Asia/Kolkata",
        "UTC",
        "ABC5",
        "XYZ-9",
        "Asia/Kathmandu",
        "Pacific/Marquesas",
        "<+07>-7",
    ):
        set_tz(tz)
        name = web_paths.resolve(env).scan_db.name
        assert re.fullmatch(r"scan-[a-z0-9-]+\.db", name), name


def test_scan_db_name_survives_a_dst_transition(env, set_tz, monkeypatch):
    """Standard and DST readings of one zone must share a DB: the engine already
    applies the right offset per timestamp, so a rename here would only throw the
    cache away twice a year.

    Feeds back the offset from the *other* side of the boundary, so the check is
    real whichever season the suite happens to run in.
    """
    set_tz("America/Los_Angeles")
    if time.daylight == 0:
        pytest.skip("no DST rule available for America/Los_Angeles here")

    def posix_offset(seconds_east: int) -> str:
        sign = "-" if seconds_east < 0 else "+"
        minutes = abs(seconds_east) // 60
        return f"{sign}{minutes // 60:02d}{minutes % 60:02d}"

    now = time.strftime("%z")
    standard = posix_offset(-time.timezone)
    daylight = posix_offset(-time.altzone)
    other = daylight if now == standard else standard
    assert other != now, "could not find the opposite-season offset"

    this_season = web_paths.resolve(env).scan_db.name
    real_strftime = time.strftime
    monkeypatch.setattr(
        time,
        "strftime",
        lambda fmt, *a: other if fmt == "%z" else real_strftime(fmt, *a),
    )
    assert web_paths.resolve(env).scan_db.name == this_season, (
        "the DB name follows the *current* UTC offset, so it would rename itself "
        "at every DST transition and discard the scan cache"
    )


def test_scan_db_name_encodes_both_the_standard_and_dst_offsets(env, set_tz):
    """The offset-independence property, stated without monkeypatching.

    A name built from the current offset can only carry one of these two, so this
    also catches a datetime.now().astimezone().utcoffset() implementation that
    the strftime patch above would miss. The offsets come from the observable
    (time.timezone / time.altzone); only the encoding is borrowed.
    """
    set_tz("America/Los_Angeles")
    if time.daylight == 0:
        pytest.skip("no DST rule available for America/Los_Angeles here")
    standard = web_paths._offset(-time.timezone)
    daylight = web_paths._offset(-time.altzone)
    assert standard != daylight
    name = web_paths.resolve(env).scan_db.name
    assert standard in name and daylight in name, (
        f"{name} carries only one offset, so it cannot be DST-stable"
    )
