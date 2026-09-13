"""The web-thread detail helper.

This is the one surface in the diff that constructs a CompanionStore, a
PokeAPI and a SpriteStore on the HTTP thread, next to a poll thread that owns
the same files. Every route test monkeypatches it and every store test builds
its own store, so without these the actual cross-thread code never runs.
"""

from __future__ import annotations

import json

import pytest

from poketokenbar import pokeapi, save
from poketokenbar.companion import CompanionState
from poketokenweb import detail, paths as web_paths

METADATA = {
    "species_id": 25,
    "types": ["electric"],
    "base_stats": {"hp": 35, "attack": 55, "defense": 40,
                   "special_attack": 50, "special_defense": 50, "speed": 90},
    "abilities": ["static"],
    "hidden_abilities": ["lightning-rod"],
    "gender_rate": 4,
    "moves": [{"name": "thunder-shock", "level": 1}],
    "version_group": "black-white",
}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """These tests are about file side effects, not about PokeAPI.

    Without this they reach the real service: the pod running them has egress,
    so the first version silently spent two HTTP round trips and a sprite
    download per test and asserted against whatever came back.
    """
    monkeypatch.setattr(
        pokeapi.PokeAPI, "metadata", lambda self, species_id: dict(METADATA)
    )
    monkeypatch.setattr(
        pokeapi.PokeAPI,
        "species",
        lambda self, species_id: {"names": [{"language": {"name": "en"}, "name": "Pikachu"}]},
    )
    monkeypatch.setattr(
        "poketokenbar.sprites.SpriteStore.path", lambda self, *a, **k: None
    )


@pytest.fixture
def offline(monkeypatch):
    """PokeAPI unreachable — an expected state for a restricted deployment."""
    def explode(self, species_id):
        raise pokeapi.PokeAPIError("offline")

    monkeypatch.setattr(pokeapi.PokeAPI, "metadata", explode)


def _paths(tmp_path) -> web_paths.Paths:
    resolved = web_paths.resolve(
        {
            "POKETOKENWEB_DATA_DIR": str(tmp_path / "data"),
            "POKETOKENWEB_WEB_ROOT": str(tmp_path / "web"),
            "POKETOKENWEB_SPOOL_DIR": str(tmp_path / "spool"),
        }
    )
    resolved.ensure()
    return resolved


def _snapshot(directory) -> dict:
    return {
        p.name: (p.stat().st_mtime_ns, p.stat().st_size)
        for p in sorted(directory.iterdir())
        if p.is_file()
    }


# --- id validation ----------------------------------------------------------


def test_only_a_plausible_species_id_is_accepted():
    assert detail.is_valid_species_id("1")
    assert detail.is_valid_species_id("649")
    assert not detail.is_valid_species_id("0")
    assert not detail.is_valid_species_id("-1")
    assert not detail.is_valid_species_id("abc")
    assert not detail.is_valid_species_id("")
    assert not detail.is_valid_species_id("1.5")


def test_the_ceiling_is_near_the_real_species_count_not_arbitrary():
    """An id PokeAPI does not have caches nothing, so every request for one
    replays upstream forever -- and this route has no authentication."""
    assert detail.is_valid_species_id("1025")
    assert not detail.is_valid_species_id("100000")
    assert detail.MAX_SPECIES_ID <= 2_000


# --- the save must not be touched from the web thread -----------------------


def test_serving_a_detail_page_does_not_write_the_save_directory(tmp_path):
    paths = _paths(tmp_path)
    save.save(CompanionState(), paths.save_file)
    before = _snapshot(paths.save_file.parent)

    assert detail.payload(paths, 25) is not None
    assert _snapshot(paths.save_file.parent) == before


def test_a_legacy_save_is_not_migrated_by_an_http_request(tmp_path):
    """load() writes a one-time backup for a pre-profiles save. That belongs to
    the owning writer, not to whoever happens to open a detail page."""
    paths = _paths(tmp_path)
    paths.save_file.write_text(json.dumps({"used_since_install": 42}), encoding="utf-8")
    before = _snapshot(paths.save_file.parent)

    assert detail.payload(paths, 25) is not None

    assert _snapshot(paths.save_file.parent) == before
    assert not list(paths.save_file.parent.glob("*pre-profiles*"))


def test_an_unreadable_save_is_not_quarantined_by_an_http_request(tmp_path):
    """_quarantine RENAMES the live save, and it needs no file descriptor -- so
    it succeeds in exactly the conditions where the read failed. An
    unauthenticated GET must not be able to do that."""
    paths = _paths(tmp_path)
    paths.save_file.write_text("[not, an, object]", encoding="utf-8")

    # It still answers -- species data does not depend on the save.
    assert detail.payload(paths, 25) is not None

    assert paths.save_file.is_file(), "the save must still be where it was"
    assert not list(paths.save_file.parent.glob("*.corrupt"))


def test_a_torn_save_is_not_quarantined_either(tmp_path):
    paths = _paths(tmp_path)
    paths.save_file.write_text('{"used_since_install": 4', encoding="utf-8")

    assert detail.payload(paths, 25) is not None
    assert paths.save_file.is_file()
    assert not list(paths.save_file.parent.glob("*.corrupt"))


def test_a_missing_save_is_not_an_error(tmp_path):
    paths = _paths(tmp_path)
    detail.payload(paths, 25)
    assert not paths.save_file.exists(), "a read must not create one"


def test_unreachable_species_data_yields_no_page_and_no_writes(tmp_path, offline):
    """An offline deployment is an expected state, not a bug -- and it must not
    leave anything behind either."""
    paths = _paths(tmp_path)
    save.save(CompanionState(), paths.save_file)
    before = _snapshot(paths.save_file.parent)

    assert detail.payload(paths, 25) is None
    assert _snapshot(paths.save_file.parent) == before
