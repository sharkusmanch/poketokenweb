"""How much of the Pokedex the companion pool draws from.

PokeAPI serves all nine generations (1025 species). The engine caps the pool at
649 -- the end of Gen V -- because that is exactly where the Black/White
ANIMATED sprite set stops, and the macOS app this descends from lives in a menu
bar where an animated sprite is the whole point. Static art exists for every
species, and poketokenbar.sprites already falls back to it past MAX_ANIMATED_ID,
so the cap is an aesthetic choice rather than a data limitation.

This makes it configurable while defaulting to the engine's behaviour, so an
adopter opting in to Gen VI-IX does not change the game for anyone who did not.

Both engine uses of MAX_SPECIES_ID read the module global at call time -- the
GraphQL query that builds the hatch pool, and the evolution-chain filter -- so
rebinding it here reaches both without editing the vendored engine.

Raising the cap has a real consequence beyond more species: the rarity curve is
weighted by capture_rate, and later generations are legendary-dense, so the odds
of everything shift. That is the caller's choice to make.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from poketokenbar import pokeapi

# The engine's own value: Gen I-V, matching the animated sprite set.
DEFAULT_MAX_SPECIES_ID = 649
# Below this there is no pool to hatch from.
MIN_MAX_SPECIES_ID = 1

# The engine caches the base-species index here, keyed on nothing at all. It is
# built from a GraphQL query bounded by the cap, so a cap change makes it stale.
INDEX_FILENAME = "base-species.json"
# Records the cap the cached index was built under.
MARKER_FILENAME = "species-cap"

# Named once so the runner can log it without drifting from the parser.
ENV_NAME = "POKETOKENWEB_MAX_SPECIES_ID"


def configured_max(env: Mapping[str, str] | None = None) -> int:
    """The effective cap. Falls back to the engine default on anything odd."""
    env = os.environ if env is None else env
    raw = (env.get(ENV_NAME) or "").strip()
    if not raw:
        return DEFAULT_MAX_SPECIES_ID
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_SPECIES_ID
    if value < MIN_MAX_SPECIES_ID:
        return DEFAULT_MAX_SPECIES_ID
    return value


def _invalidate_stale_index(cache_dir: Path, cap: int) -> bool:
    """Drop the cached index when it was built under a different cap.

    Without this the setting appears to do nothing: base_species_index returns
    the cached file before it ever builds a query, so a raised cap would sit
    inert until somebody deleted the cache by hand.
    """
    marker = cache_dir / MARKER_FILENAME
    try:
        previous = marker.read_text(encoding="utf-8").strip()
    except OSError:
        previous = ""

    changed = previous != str(cap)
    if not changed:
        return False

    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / INDEX_FILENAME).unlink(missing_ok=True)
        marker.write_text(str(cap), encoding="utf-8")
    except OSError:
        # A cache we cannot rewrite is not worth failing startup over; the
        # index simply rebuilds or stays as it is.
        return False
    return changed


def apply(env: Mapping[str, str] | None = None, cache_dir: Path | None = None) -> int:
    """Install the configured cap and clear a cached index built under another.

    Returns the effective cap so the caller can log it.
    """
    cap = configured_max(env)
    pokeapi.MAX_SPECIES_ID = cap
    if cache_dir is not None:
        _invalidate_stale_index(Path(cache_dir), cap)
    return cap
