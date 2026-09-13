"""PokéAPI access — ports PokeAPIClient.swift.

Species data is fetched at runtime and cached on disk; nothing Pokémon-related
is bundled in the repository.

Everything here is best effort. If the network is down the caller keeps the
tokens in the egg and hatches later — progress is never discarded.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from dataclasses import dataclass
from pathlib import Path

from .balance import DITTO_SPECIES_ID, Rarity
from .companion import EvoLine

REST_BASE = "https://pokeapi.co/api/v2"
GRAPHQL_URL = "https://graphql.pokeapi.co/v1beta2"


def _endpoint_origin() -> str:
    """Scheme and host of REST_BASE, with a trailing slash.

    Read at call time so REST_BASE may be repointed at a self-hosted instance.
    """
    parts = urlsplit(REST_BASE)
    return f"{parts.scheme}://{parts.netloc}/"
# Gen I-V. The animated Black/White sprites the panel uses stop here.
MAX_SPECIES_ID = 649
LANG_CODES = ("ko", "en", "ja-Hrkt", "ja", "es")
# Which game's level-up learnset to keep. Black/White matches the animated
# sprite set this app draws from, and keeping ONE version group is what turns a
# ~300 KB /pokemon response into a few KB on disk: PokeAPI ships every move's
# entire cross-generation history, and none of it is rendered.
SUPPORTED_VERSION_GROUP = "black-white"
# PokéAPI's GraphQL endpoint answers 403 to urllib's default User-Agent.
USER_AGENT = "poketokenbar/0.1 (+https://github.com/chattymin/PokeTokenBar)"


class PokeAPIError(Exception):
    pass


@dataclass(slots=True)
class BaseSpecies:
    id: int
    capture_rate: int


def _get_json(url: str, timeout: float = 15.0):
    request = urllib.request.Request(url)
    request.add_header("User-Agent", USER_AGENT)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        raise PokeAPIError(f"GET {url}: {exc}") from exc


def _post_json(url: str, payload: dict, timeout: float = 20.0):
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body)
    request.add_header("Content-Type", "application/json")
    request.add_header("User-Agent", USER_AGENT)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        raise PokeAPIError(f"POST {url}: {exc}") from exc


class PokeAPI:
    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = cache_dir or (Path.home() / ".cache" / "poketokenbar")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._species: dict[int, dict] = {}
        self._lines: dict[int, EvoLine] = {}

    # --- hatch candidates --------------------------------------------------

    @property
    def _index_file(self) -> Path:
        return self.cache_dir / "base-species.json"

    def base_species_index(self) -> list[BaseSpecies]:
        """Every Gen I-V evolution-line start, with its capture rate.

        One GraphQL query, cached to disk. Ditto is excluded from the normal
        pool; it only appears through the disguise mechanic.
        """
        if self._index_file.is_file():
            try:
                raw = json.loads(self._index_file.read_text(encoding="utf-8"))
                if raw:
                    return [BaseSpecies(r["id"], r["capture_rate"]) for r in raw]
            except (ValueError, KeyError, TypeError):
                pass  # rebuild below

        query = (
            "{ pokemonspecies(where: {evolves_from_species_id: {_is_null: true}, "
            f"id: {{_lte: {MAX_SPECIES_ID}, _neq: {DITTO_SPECIES_ID}}}}}, "
            "order_by: {id: asc}) { id capture_rate } }"
        )
        payload = _post_json(GRAPHQL_URL, {"query": query})
        rows = (payload.get("data") or {}).get("pokemonspecies") or []
        if not rows:
            raise PokeAPIError("empty base species index")

        out = [
            BaseSpecies(int(r["id"]), int(r["capture_rate"]))
            for r in rows
            if r.get("capture_rate") is not None
        ]
        tmp = self._index_file.with_suffix(".tmp")
        tmp.write_text(
            json.dumps([{"id": b.id, "capture_rate": b.capture_rate} for b in out]),
            encoding="utf-8",
        )
        tmp.replace(self._index_file)
        return out

    # --- lines -------------------------------------------------------------

    def species(self, species_id: int) -> dict:
        if species_id in self._species:
            return self._species[species_id]
        cached = self.cache_dir / "species" / f"{species_id}.json"
        if cached.is_file():
            try:
                data = json.loads(cached.read_text(encoding="utf-8"))
                self._species[species_id] = data
                return data
            except ValueError:
                pass
        data = _get_json(f"{REST_BASE}/pokemon-species/{species_id}")
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(data), encoding="utf-8")
        self._species[species_id] = data
        return data

    # --- per-species metadata (stats, types, abilities, moves) -------------

    @property
    def _metadata_dir(self) -> Path:
        return self.cache_dir / "metadata"

    def metadata(self, species_id: int) -> dict:
        """Immutable species data, distilled and cached on disk.

        Fetched on demand -- opening a detail page -- and never from the poll
        loop. The raw /pokemon document is large and almost entirely moves from
        games this app does not draw; only the supported version group is kept.

        Raises PokeAPIError when it cannot be had, so the caller can say
        "details unavailable" rather than render an empty page as if the
        creature had no stats.
        """
        cached = self._metadata_dir / f"{species_id}.json"
        if cached.is_file():
            try:
                return json.loads(cached.read_text(encoding="utf-8"))
            except ValueError:
                pass  # refetch below

        raw = _get_json(f"{REST_BASE}/pokemon/{species_id}")
        gender_rate = None
        try:
            gender_rate = self.species(species_id).get("gender_rate")
        except PokeAPIError:
            # A missing gender rate costs one row of the detail page; failing
            # the whole fetch over it would cost all of them.
            pass

        distilled = _distil_pokemon(raw, species_id, gender_rate)
        try:
            cached.parent.mkdir(parents=True, exist_ok=True)
            tmp = cached.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(distilled), encoding="utf-8")
            tmp.replace(cached)
        except OSError:
            pass  # a cache we cannot write is not a reason to fail the request
        return distilled

    def line(self, base_species_id: int) -> EvoLine:
        """The evolution line starting at base_species_id.

        Branching lines pick one path; the panel shows a single companion.
        """
        if base_species_id in self._lines:
            return self._lines[base_species_id]

        base = self.species(base_species_id)
        chain_url = (base.get("evolution_chain") or {}).get("url")
        # The chain URL arrives inside the API's own response and is then
        # fetched, so it must be constrained -- but to the endpoint actually in
        # use, not to the public host. A self-hosted instance returns URLs
        # pointing at itself, which a literal "https://pokeapi.co/" rejects.
        if not chain_url or not chain_url.startswith(_endpoint_origin()):
            raise PokeAPIError(f"bad evolution chain url for {base_species_id}")
        chain = _get_json(chain_url)

        path: list[int] = []
        node = chain.get("chain")
        while node:
            species_ref = node.get("species") or {}
            species_id = _id_from_url(species_ref.get("url", ""))
            if species_id is None or species_id > MAX_SPECIES_ID:
                break
            path.append(species_id)
            nxt = node.get("evolves_to") or []
            node = nxt[0] if nxt else None

        if not path:
            raise PokeAPIError(f"empty evolution path for {base_species_id}")

        rarity = Rarity.classify(
            int(base.get("capture_rate") or 255),
            bool(base.get("is_legendary")),
            bool(base.get("is_mythical")),
        )
        names: dict[int, dict[str, str]] = {}
        for sid in path:
            try:
                entry = self.species(sid)
            except PokeAPIError:
                continue
            by_lang = {
                n["language"]["name"]: n["name"]
                for n in entry.get("names", [])
                if n.get("language", {}).get("name") in LANG_CODES
            }
            names[sid] = by_lang

        evo = EvoLine(base_id=base_species_id, path_ids=path, rarity=rarity, names=names)
        self._lines[base_species_id] = evo
        return evo

    # --- rolling -----------------------------------------------------------

    def roll_base_species(self, rng, tier: Rarity | None = None) -> int:
        """Capture-rate-weighted pick, so commons are common.

        capture_rate runs 3 (legendary-ish) to 255 (Caterpie). Using it directly
        as the weight reproduces the official rarity curve.
        """
        candidates = self.base_species_index()
        if tier is not None:
            ceiling = tier.capture_rate_ceiling
            if ceiling is not None:
                candidates = [c for c in candidates if c.capture_rate <= ceiling]
        if not candidates:
            raise PokeAPIError("no hatch candidates")
        weights = [c.capture_rate for c in candidates]
        return rng.choices(candidates, weights=weights, k=1)[0].id


# PokeAPI names stats with hyphens; the save and the stat formula use
# underscores, and mapping in one place keeps the difference off every caller.
_STAT_NAMES = {
    "hp": "hp",
    "attack": "attack",
    "defense": "defense",
    "special-attack": "special_attack",
    "special-defense": "special_defense",
    "speed": "speed",
}


def _level_up_moves(raw: dict) -> tuple[list[dict], str]:
    """Level-up moves for one version group, earliest first.

    Prefers SUPPORTED_VERSION_GROUP. Falls back to whichever group has the most
    level-up entries, so a species that group never shipped still shows a
    learnset instead of an empty list.
    """
    by_group: dict[str, list[dict]] = {}
    for entry in raw.get("moves") or []:
        name = ((entry.get("move") or {}).get("name")) or ""
        if not name:
            continue
        for detail in entry.get("version_group_details") or []:
            method = ((detail.get("move_learn_method") or {}).get("name")) or ""
            if method != "level-up":
                continue
            group = ((detail.get("version_group") or {}).get("name")) or ""
            if not group:
                continue
            by_group.setdefault(group, []).append(
                {"name": name, "level": int(detail.get("level_learned_at") or 0)}
            )

    if not by_group:
        return [], ""
    group = (
        SUPPORTED_VERSION_GROUP
        if by_group.get(SUPPORTED_VERSION_GROUP)
        else max(by_group, key=lambda key: len(by_group[key]))
    )
    moves = sorted(by_group[group], key=lambda move: (move["level"], move["name"]))
    return moves, group


def _distil_pokemon(raw: dict, species_id: int, gender_rate) -> dict:
    base_stats: dict[str, int] = {}
    for stat in raw.get("stats") or []:
        name = ((stat.get("stat") or {}).get("name")) or ""
        key = _STAT_NAMES.get(name)
        if key is not None:
            base_stats[key] = int(stat.get("base_stat") or 0)

    abilities: list[str] = []
    hidden: list[str] = []
    for entry in raw.get("abilities") or []:
        name = ((entry.get("ability") or {}).get("name")) or ""
        if not name:
            continue
        (hidden if entry.get("is_hidden") else abilities).append(name)

    moves, group = _level_up_moves(raw)
    return {
        "species_id": species_id,
        "types": [
            ((t.get("type") or {}).get("name")) or ""
            for t in raw.get("types") or []
            if ((t.get("type") or {}).get("name"))
        ],
        "base_stats": base_stats,
        "abilities": abilities,
        "hidden_abilities": hidden,
        "gender_rate": gender_rate if isinstance(gender_rate, int) else None,
        "moves": moves,
        "version_group": group,
    }


def _id_from_url(url: str) -> int | None:
    parts = [p for p in url.rstrip("/").split("/") if p]
    if not parts:
        return None
    try:
        return int(parts[-1])
    except ValueError:
        return None
