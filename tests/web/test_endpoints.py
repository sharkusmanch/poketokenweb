"""Pointing the app at a self-hosted PokeAPI.

Three settings, four engine constants, and one guard that could not be rebound
because it was a string literal inside a function.
"""

from __future__ import annotations

import json

import pytest

from poketokenbar import pokeapi, sprites
from poketokenweb import endpoints


@pytest.fixture(autouse=True)
def restore_engine_constants():
    """Rebinding module globals leaks across tests if not put back."""
    saved = (
        pokeapi.REST_BASE,
        pokeapi.GRAPHQL_URL,
        sprites.SPRITE_BASE,
        sprites.ITEM_BASE,
    )
    yield
    (
        pokeapi.REST_BASE,
        pokeapi.GRAPHQL_URL,
        sprites.SPRITE_BASE,
        sprites.ITEM_BASE,
    ) = saved


SELF_HOSTED = "http://pokeapi.tools.svc.cluster.local/api/v2"


# --- defaults --------------------------------------------------------------

def test_defaults_match_the_engine():
    e = endpoints.configured({})
    assert e.rest == endpoints.DEFAULT_REST == "https://pokeapi.co/api/v2"
    assert e.graphql == endpoints.DEFAULT_GRAPHQL
    assert e.customised is False


def test_applying_defaults_leaves_the_engine_untouched(tmp_path):
    before = (pokeapi.REST_BASE, sprites.SPRITE_BASE, sprites.ITEM_BASE)
    endpoints.apply({}, tmp_path)
    assert (pokeapi.REST_BASE, sprites.SPRITE_BASE, sprites.ITEM_BASE) == before


# --- overriding ------------------------------------------------------------

def test_rest_base_reaches_the_engine(tmp_path):
    e = endpoints.apply({endpoints.REST_ENV: SELF_HOSTED}, tmp_path)
    assert e.rest == SELF_HOSTED
    assert pokeapi.REST_BASE == SELF_HOSTED
    assert e.customised is True


def test_graphql_url_reaches_the_engine(tmp_path):
    endpoints.apply({endpoints.GRAPHQL_ENV: "http://hasura.local/v1/graphql"}, tmp_path)
    assert pokeapi.GRAPHQL_URL == "http://hasura.local/v1/graphql"


def test_one_sprite_root_derives_both_engine_constants(tmp_path):
    endpoints.apply({endpoints.SPRITE_ENV: "http://sprites.local/sprites"}, tmp_path)
    assert sprites.SPRITE_BASE == "http://sprites.local/sprites/pokemon"
    assert sprites.ITEM_BASE == "http://sprites.local/sprites/items"


def test_a_trailing_slash_does_not_double_up(tmp_path):
    endpoints.apply({endpoints.SPRITE_ENV: "http://sprites.local/sprites/"}, tmp_path)
    assert sprites.SPRITE_BASE == "http://sprites.local/sprites/pokemon"


def test_the_derived_sprite_urls_are_what_the_engine_requests(tmp_path):
    endpoints.apply({endpoints.SPRITE_ENV: "http://sprites.local/s"}, tmp_path)
    assert sprites.sprite_url(25, animated=False, shiny=False) == (
        "http://sprites.local/s/pokemon/25.png"
    )
    assert sprites.sprite_url(25, animated=False, shiny=True) == (
        "http://sprites.local/s/pokemon/shiny/25.png"
    )


# --- rejecting junk --------------------------------------------------------

@pytest.mark.parametrize("bad", ["pokeapi.co/api/v2", "ftp://host/x", "://x", "not a url"])
def test_a_non_http_url_falls_back_and_is_reported(bad, tmp_path):
    # Reported, because a silent fallback to the PUBLIC api is precisely what
    # someone running an offline instance would never notice.
    e = endpoints.apply({endpoints.REST_ENV: bad}, tmp_path)
    assert e.rest == endpoints.DEFAULT_REST
    assert any(endpoints.REST_ENV in r for r in e.rejected)


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_blank_is_simply_unset_not_an_error(blank, tmp_path):
    env = {} if blank is None else {endpoints.REST_ENV: blank}
    e = endpoints.apply(env, tmp_path)
    assert e.rest == endpoints.DEFAULT_REST
    assert e.rejected == ()


# --- the guard that could not be rebound -----------------------------------
# A chain URL arrives inside the API's own response and is then fetched, so it
# must be constrained. The engine constrained it to the literal public host,
# which rejects every self-hosted response.

def _seed_species(cache_dir, chain_host):
    """Pre-seed the on-disk species cache so line() needs no network to reach
    the guard -- the guard is the thing under test, not the fetch."""
    species_dir = cache_dir / "species"
    species_dir.mkdir(parents=True, exist_ok=True)
    (species_dir / "1.json").write_text(
        json.dumps({"evolution_chain": {"url": f"{chain_host}/api/v2/evolution-chain/1/"}})
    )


def _api(cache_dir):
    return pokeapi.PokeAPI(cache_dir=cache_dir)


def test_a_self_hosted_chain_url_passes_the_real_guard(tmp_path, monkeypatch):
    """Drives PokeAPI.line(), not a re-implementation of its check.

    An earlier version of this test asserted on _endpoint_origin() and did the
    startswith() itself -- it passed even when the guard was deleted outright.
    """
    endpoints.apply({endpoints.REST_ENV: SELF_HOSTED}, tmp_path)
    _seed_species(tmp_path, "http://pokeapi.tools.svc.cluster.local")

    fetched = []

    def fake_get(url, *a, **k):
        fetched.append(url)
        return {"chain": {"species": {"url": f"{SELF_HOSTED}/pokemon-species/1/"},
                          "evolves_to": []}}

    monkeypatch.setattr(pokeapi, "_get_json", fake_get)

    _api(tmp_path).line(1)   # must not raise

    assert fetched == ["http://pokeapi.tools.svc.cluster.local/api/v2/evolution-chain/1/"]


def test_a_foreign_chain_url_is_refused_by_the_real_guard(tmp_path, monkeypatch):
    """The guard's point: a response must not send us to another host."""
    endpoints.apply({endpoints.REST_ENV: SELF_HOSTED}, tmp_path)
    _seed_species(tmp_path, "https://evil.example.com")

    def fail(url, *a, **k):  # pragma: no cover - must never run
        raise AssertionError(f"guard let us fetch {url}")

    monkeypatch.setattr(pokeapi, "_get_json", fail)

    with pytest.raises(pokeapi.PokeAPIError):
        _api(tmp_path).line(1)


def test_the_public_host_is_refused_once_a_private_one_is_configured(
    tmp_path, monkeypatch
):
    endpoints.apply({endpoints.REST_ENV: SELF_HOSTED}, tmp_path)
    _seed_species(tmp_path, "https://pokeapi.co")
    monkeypatch.setattr(pokeapi, "_get_json", lambda *a, **k: {})

    with pytest.raises(pokeapi.PokeAPIError):
        _api(tmp_path).line(1)


def test_the_default_endpoint_still_accepts_public_chain_urls(tmp_path, monkeypatch):
    endpoints.apply({}, tmp_path)
    _seed_species(tmp_path, "https://pokeapi.co")
    monkeypatch.setattr(
        pokeapi, "_get_json",
        lambda *a, **k: {"chain": {"species": {"url": f"{endpoints.DEFAULT_REST}/pokemon-species/1/"},
                                   "evolves_to": []}},
    )

    _api(tmp_path).line(1)   # must not raise


def test_the_port_is_part_of_the_origin(tmp_path, monkeypatch):
    """Both directions, because either alone is passed by a broken origin.

    An origin built from the hostname only rejects the 9000 case just as
    correctly -- it is the 8000 case it gets wrong.
    """
    endpoints.apply({endpoints.REST_ENV: "http://pokeapi.local:8000/api/v2"}, tmp_path)
    monkeypatch.setattr(
        pokeapi, "_get_json",
        lambda *a, **k: {"chain": {"species": {"url": "http://pokeapi.local:8000/api/v2/pokemon-species/1/"},
                                   "evolves_to": []}},
    )

    _seed_species(tmp_path, "http://pokeapi.local:8000")
    _api(tmp_path).line(1)   # the configured port must be accepted

    _seed_species(tmp_path, "http://pokeapi.local:9000")
    with pytest.raises(pokeapi.PokeAPIError):
        _api(tmp_path).line(1)   # another port on the same host must not be


# --- cache invalidation ----------------------------------------------------
# Cached species documents embed an ABSOLUTE evolution_chain URL, so keeping
# them across an endpoint change would send the app back to the old host.

def _seed(cache_dir, host="https://pokeapi.co"):
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / endpoints.INDEX_FILENAME).write_text("[]")
    species = cache_dir / endpoints.SPECIES_DIRNAME
    species.mkdir(exist_ok=True)
    (species / "25.json").write_text(
        json.dumps({"evolution_chain": {"url": f"{host}/api/v2/evolution-chain/10/"}})
    )
    return species / "25.json"


def test_changing_the_endpoint_drops_species_documents(tmp_path):
    doc = _seed(tmp_path)
    (tmp_path / endpoints.MARKER_FILENAME).write_text(endpoints.DEFAULT_REST)

    endpoints.apply({endpoints.REST_ENV: SELF_HOSTED}, tmp_path)

    assert not doc.exists()
    assert not (tmp_path / endpoints.INDEX_FILENAME).exists()
    assert (tmp_path / endpoints.MARKER_FILENAME).read_text() == SELF_HOSTED


def test_an_unchanged_endpoint_keeps_the_cache(tmp_path):
    doc = _seed(tmp_path)
    (tmp_path / endpoints.MARKER_FILENAME).write_text(endpoints.DEFAULT_REST)

    endpoints.apply({}, tmp_path)

    assert doc.exists()
    assert (tmp_path / endpoints.INDEX_FILENAME).exists()


def test_a_cache_from_before_this_feature_is_dropped_once(tmp_path):
    # No marker: the documents cannot be trusted to match the current endpoint.
    doc = _seed(tmp_path)
    endpoints.apply({}, tmp_path)
    assert not doc.exists()
    assert (tmp_path / endpoints.MARKER_FILENAME).read_text() == endpoints.DEFAULT_REST


def test_sprites_survive_an_endpoint_change(tmp_path):
    # Content-addressed by species id; nothing host-specific is baked in.
    sprite_dir = tmp_path / "sprites"
    sprite_dir.mkdir(parents=True)
    kept = sprite_dir / "25-s.png"
    kept.write_bytes(b"\x89PNG")

    endpoints.apply({endpoints.REST_ENV: SELF_HOSTED}, tmp_path)

    assert kept.exists()


def test_an_unwritable_cache_does_not_fail_startup(tmp_path):
    blocked = tmp_path / "file"
    blocked.write_text("not a directory")
    e = endpoints.apply({endpoints.REST_ENV: SELF_HOSTED}, blocked / "cache")
    assert e.rest == SELF_HOSTED
