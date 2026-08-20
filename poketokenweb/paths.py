"""Explicit, env-driven filesystem layout for the containerised web app.

In the container HOME is a **read-only** mount — it carries the user's Claude
and Codex logs and nothing else. So nothing here may fall back to
``Path.home()``: every writable location is resolved from an environment
variable with a container-shaped default, and the vendored engine classes that
*do* hardcode ``Path.home() / ".cache" / "poketokenbar"``
(:class:`poketokenbar.sprites.SpriteStore`, :class:`poketokenbar.pokeapi.PokeAPI`)
must always be constructed with an explicit ``cache_dir`` taken from here.

The scan-DB filename carries a timezone slug; see :func:`effective_timezone_slug`
for why it is derived from what Python actually resolves rather than from ``TZ``.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path

DATA_DIR_ENV = "POKETOKENWEB_DATA_DIR"
WEB_ROOT_ENV = "POKETOKENWEB_WEB_ROOT"
SPOOL_DIR_ENV = "POKETOKENWEB_SPOOL_DIR"

DEFAULT_DATA_DIR = "/data"
DEFAULT_WEB_ROOT = "/app/web"
DEFAULT_SPOOL_DIR = "/tmp/poketokenbar/commands"

_UNSAFE = re.compile(r"[^a-z0-9]+")


def _slug(text: str) -> str:
    """Lowercase, filesystem-safe, never empty."""
    out = _UNSAFE.sub("-", text.lower()).strip("-")
    return out[:48] or "unknown"


def _offset(seconds: int) -> str:
    """UTC offset as ``p0900`` / ``m0800``.

    Letters rather than ``+``/``-`` because the slugifier folds ``+`` into ``-``,
    which would collide +09:00 with -09:00.
    """
    sign = "m" if seconds < 0 else "p"
    total = abs(int(seconds)) // 60
    return f"{sign}{total // 60:02d}{total % 60:02d}"


def effective_timezone_slug() -> str:
    """A slug for the timezone this process *actually* resolves.

    The engine stamps ``local_day`` onto every parsed entry using
    ``date.astimezone()`` and stores it in the SQLite scan cache, which is keyed
    only on (provider, path, mtime, size, parser_version). Change the timezone
    and the cache silently mixes two eras of day bucketing — so the DB filename
    has to change with the timezone.

    Deliberately *not* the ``TZ`` env var: with ``TZ=America/Los_Angeles`` but
    tzdata missing from the image, Python buckets days as UTC while ``TZ`` still
    claims Pacific, and a TZ-keyed filename would happily reuse a UTC-bucketed
    cache. ``time.tzname`` and ``time.timezone``/``time.altzone`` are what libc
    resolved, so they follow the real bucketing.

    The *standard* and DST offsets are used rather than the current one: the
    engine already applies the right offset per timestamp, so renaming the DB at
    each DST transition would throw the cache away twice a year for nothing.
    """
    names: list[str] = []
    for name in time.tzname:
        if name and name not in names:
            names.append(name)
    standard = -time.timezone
    daylight = -time.altzone if time.daylight else standard
    return _slug("-".join([*names, _offset(standard), _offset(daylight)]))


@dataclass(frozen=True)
class Paths:
    state_file: Path
    config_file: Path
    save_file: Path
    events_file: Path
    heartbeat_file: Path
    cache_dir: Path
    sprite_dir: Path
    scan_db: Path
    spool_dir: Path
    web_root: Path

    #: Directories the app writes into. ``web_root`` is absent on purpose: it
    #: ships inside the read-only image, and creating it would mask a bad mount.
    WRITABLE_DIRS = ("cache_dir", "sprite_dir", "spool_dir")
    FILES = (
        "state_file",
        "config_file",
        "save_file",
        "events_file",
        "heartbeat_file",
        "scan_db",
    )

    def ensure(self) -> None:
        """Create every writable directory, including the parents of the files."""
        for name in self.FILES:
            getattr(self, name).parent.mkdir(parents=True, exist_ok=True)
        for name in self.WRITABLE_DIRS:
            getattr(self, name).mkdir(parents=True, exist_ok=True)

    def as_dict(self) -> dict[str, Path]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


def _from_env(env: Mapping[str, str], key: str, default: str) -> Path:
    # `or default` rather than dict.get's default: k8s renders an unset var as
    # an empty string, which must not become Path("").
    return Path(env.get(key) or default)


def resolve(env: Mapping[str, str] | None = None) -> Paths:
    """Resolve the whole layout from ``env`` (defaults to ``os.environ``)."""
    values: Mapping[str, str] = os.environ if env is None else env

    data = _from_env(values, DATA_DIR_ENV, DEFAULT_DATA_DIR)
    state = data / "state"
    cache = data / "cache"

    return Paths(
        state_file=state / "state.json",
        config_file=data / "config" / "config.json",
        save_file=data / "save" / "companion.json",
        events_file=state / "events.json",
        heartbeat_file=state / "heartbeat",
        cache_dir=cache,
        # SpriteStore appends "sprites" to whatever cache_dir it is handed; this
        # must stay in lockstep or the server serves an empty folder.
        sprite_dir=cache / "sprites",
        scan_db=cache / f"scan-{effective_timezone_slug()}.db",
        spool_dir=_from_env(values, SPOOL_DIR_ENV, DEFAULT_SPOOL_DIR),
        web_root=_from_env(values, WEB_ROOT_ENV, DEFAULT_WEB_ROOT),
    )
