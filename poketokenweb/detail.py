"""Serving one Pokemon's detail page.

The companion store lives on the poll thread and owns the save file. A detail
request arrives on the web thread, so it builds its own short-lived store
instead of reaching across. The store is constructed ``read_only``, which is
not a comment but a switch: it loads without the quarantine-and-backup side
effects ``save.load`` normally has, and its ``_persist`` is a no-op. Without
that, an unauthenticated GET could rename the user's live save away the moment
one read hit a transient OSError.

It is also safe because the derivation is deterministic: gender and ability are
seeded from the individual's own IVs, so the throwaway store computes the same
creature the poll thread would, with no shared state and no second writer.
"""

from __future__ import annotations

from poketokenbar import config as engine_config
from poketokenbar.companion_store import CompanionStore
from poketokenbar.pokeapi import PokeAPI
from poketokenbar.sprites import SpriteStore

from .paths import Paths

# PokeAPI serves ~1025 species. The ceiling is generous rather than exact so a
# raised pool cap needs no second edit, but it is nowhere near the 100k it was:
# an id PokeAPI does not have caches NOTHING, so every request for one replays
# upstream forever, and this route is unauthenticated.
MAX_SPECIES_ID = 2_000


def is_valid_species_id(raw: str) -> bool:
    """Whether this path segment could name a species at all.

    Rejected before anything touches the network or the disk: a request for
    "../../etc" or a 40-digit number must not become a PokeAPI fetch.
    """
    if not raw.isdigit():
        return False
    value = int(raw)
    return 1 <= value <= MAX_SPECIES_ID


def payload(paths: Paths, species_id: int) -> dict | None:
    """The detail page for one species, or None when it cannot be assembled."""
    # Difficulty scales the threshold a level is measured against, so a store
    # built at the default would report a different level here than the home
    # screen does for the same creature.
    settings = engine_config.load(paths.config_file)
    store = CompanionStore(
        save_path=paths.save_file,
        api=PokeAPI(cache_dir=paths.cache_dir),
        sprite_store=SpriteStore(cache_dir=paths.cache_dir),
        growth_difficulty=settings.get("growth_difficulty", 1.0),
        shop_difficulty=settings.get("shop_difficulty", 1.0),
        read_only=True,
    )
    return store.detail_payload(species_id)
