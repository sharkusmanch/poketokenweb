"""Serving one Pokemon's detail page.

The companion store lives on the poll thread and owns the save file. A detail
request arrives on the web thread, so it builds its own short-lived store
instead of reaching across: the store is read-only in this direction, because
``detail_payload`` derives everything it needs and never writes.

That is only safe because the derivation is deterministic. Gender and ability
are seeded from the individual's own IVs, so the throwaway store computes the
same creature the poll thread would, with no shared state and no second writer.
"""

from __future__ import annotations

from poketokenbar.companion_store import CompanionStore
from poketokenbar.pokeapi import PokeAPI
from poketokenbar.sprites import SpriteStore

from .paths import Paths

# Species ids are small positive integers; PokeAPI has ~1025. The ceiling is
# generous rather than exact so raising the pool cap does not need a second
# edit here, but it still stops an unbounded id from becoming a fetch.
MAX_SPECIES_ID = 100_000


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
    store = CompanionStore(
        save_path=paths.save_file,
        api=PokeAPI(cache_dir=paths.cache_dir),
        sprite_store=SpriteStore(cache_dir=paths.cache_dir),
    )
    return store.detail_payload(species_id)
