"""Where species data and sprites come from.

The engine hardcodes the public PokeAPI and the PokeAPI/sprites CDN. Anyone
running their own instance -- for offline operation, or simply to not depend on
a public service -- needs to point it elsewhere. Defaults are the engine's own
values, so nothing changes unless a setting is given.

Three URLs, four constants: PokeAPI serves REST and GraphQL from separate
hosts, and the sprite repository holds species art and item art under one root,
so a single sprite base derives both.

All four engine constants are read at call time, so rebinding them here reaches
every use without editing the vendored engine. One thing could not be done that
way -- see the note on the evolution-chain guard below.

CACHE COUPLING: cached species documents embed absolute evolution_chain URLs
pointing at whichever host served them. Left in place across an endpoint change
they would send the app back to the old host, which the chain guard then
rejects -- so the PokeAPI caches are dropped when the base URL changes. Sprites
are content-addressed by species id and survive.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from poketokenbar import pokeapi, sprites

REST_ENV = "POKETOKENWEB_POKEAPI_BASE_URL"
GRAPHQL_ENV = "POKETOKENWEB_POKEAPI_GRAPHQL_URL"
SPRITE_ENV = "POKETOKENWEB_SPRITE_BASE_URL"

DEFAULT_REST = "https://pokeapi.co/api/v2"
DEFAULT_GRAPHQL = "https://graphql.pokeapi.co/v1beta2"
# The engine's two sprite constants share this parent.
DEFAULT_SPRITE_ROOT = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites"

# Records the REST base the cached species documents were fetched from.
MARKER_FILENAME = "pokeapi-endpoint"
INDEX_FILENAME = "base-species.json"
SPECIES_DIRNAME = "species"


@dataclass(frozen=True, slots=True)
class Endpoints:
    rest: str
    graphql: str
    sprite_root: str
    rejected: tuple[str, ...] = ()

    @property
    def customised(self) -> bool:
        return (
            self.rest != DEFAULT_REST
            or self.graphql != DEFAULT_GRAPHQL
            or self.sprite_root != DEFAULT_SPRITE_ROOT
        )


def _clean(raw: object, default: str, name: str, rejected: list[str]) -> str:
    """An absolute http(s) URL, or the default with a note for the caller.

    Falling back rather than raising: a typo should cost the custom endpoint,
    not prevent the app from starting -- but it must be reported, because a
    silent fallback to the public API is exactly what someone running an
    offline instance would never notice.
    """
    if not isinstance(raw, str):
        return default
    value = raw.strip().rstrip("/")
    if not value:
        return default
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        rejected.append(f"{name}={raw!r} is not an absolute http(s) URL")
        return default
    return value


def configured(env: Mapping[str, str] | None = None) -> Endpoints:
    env = os.environ if env is None else env
    rejected: list[str] = []
    return Endpoints(
        rest=_clean(env.get(REST_ENV), DEFAULT_REST, REST_ENV, rejected),
        graphql=_clean(env.get(GRAPHQL_ENV), DEFAULT_GRAPHQL, GRAPHQL_ENV, rejected),
        sprite_root=_clean(env.get(SPRITE_ENV), DEFAULT_SPRITE_ROOT, SPRITE_ENV, rejected),
        rejected=tuple(rejected),
    )


def _invalidate_stale_cache(cache_dir: Path, rest: str) -> bool:
    """Drop PokeAPI caches fetched from a different host.

    Not merely stale but actively wrong: a cached species document carries an
    absolute evolution_chain URL, so keeping it would make the app fetch from
    the previous endpoint.
    """
    marker = cache_dir / MARKER_FILENAME
    try:
        previous = marker.read_text(encoding="utf-8").strip()
    except OSError:
        previous = ""

    if previous == rest:
        return False

    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / INDEX_FILENAME).unlink(missing_ok=True)
        shutil.rmtree(cache_dir / SPECIES_DIRNAME, ignore_errors=True)
        marker.write_text(rest, encoding="utf-8")
    except OSError:
        # A cache we cannot rewrite is not worth refusing to boot over.
        return False
    return True


def apply(env: Mapping[str, str] | None = None, cache_dir: Path | None = None) -> Endpoints:
    """Install the configured endpoints. Returns them so the caller can log."""
    resolved = configured(env)
    pokeapi.REST_BASE = resolved.rest
    pokeapi.GRAPHQL_URL = resolved.graphql
    sprites.SPRITE_BASE = f"{resolved.sprite_root}/pokemon"
    sprites.ITEM_BASE = f"{resolved.sprite_root}/items"
    if cache_dir is not None:
        _invalidate_stale_cache(Path(cache_dir), resolved.rest)
    return resolved
