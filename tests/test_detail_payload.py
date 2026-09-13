"""The Pokedex detail page — upstream #264, store side."""

from __future__ import annotations

import random

from poketokenbar import balance, shop
from poketokenbar.balance import Rarity
from poketokenbar.companion import DexEntry, EvoLine, apply_usage
from poketokenbar.companion_store import CompanionStore

LINE = EvoLine(base_id=1, path_ids=[1, 2, 3], rarity=Rarity.COMMON)

METADATA = {
    "species_id": 1,
    "types": ["grass", "poison"],
    "base_stats": {
        "hp": 45,
        "attack": 49,
        "defense": 49,
        "special_attack": 65,
        "special_defense": 65,
        "speed": 45,
    },
    "abilities": ["overgrow", "chlorophyll"],
    "hidden_abilities": ["solar-power"],
    "gender_rate": 1,
    "moves": [{"name": "tackle", "level": 1}, {"name": "vine-whip", "level": 13}],
    "version_group": "black-white",
}


class FakeAPI:
    """Serves metadata for any species, and counts how often it is asked."""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.metadata_calls = 0

    def roll_base_species(self, rng, tier=None):
        return 1

    def line(self, base_id):
        return LINE

    def species(self, species_id):
        return {"names": [{"language": {"name": "en"}, "name": f"Mon{species_id}"}]}

    def metadata(self, species_id):
        self.metadata_calls += 1
        if self.fail:
            raise RuntimeError("offline")
        return {**METADATA, "species_id": species_id}


def _store(tmp_path, api=None) -> CompanionStore:
    store = CompanionStore(
        save_path=tmp_path / "c.json", api=api or FakeAPI(), rng=random.Random(3)
    )
    store.update({}, today="2026-09-13")
    return store


def _hatched(tmp_path, api=None) -> CompanionStore:
    store = _store(tmp_path, api)
    store.update({"claude_code": balance.EGG_HATCH_THRESHOLD}, today="2026-09-13")
    return store


# --- the happy path ---------------------------------------------------------


def test_the_active_companion_has_a_detail_page(tmp_path):
    store = _hatched(tmp_path)
    detail = store.detail_payload(1)
    assert detail is not None
    assert detail["species_id"] == 1
    assert detail["types"] == ["grass", "poison"]
    assert detail["level"] == 1
    assert detail["has_individual"] is True


def test_the_page_carries_the_individuals_own_values(tmp_path):
    store = _hatched(tmp_path)
    detail = store.detail_payload(1)
    assert set(detail["ivs"]) == {
        "hp",
        "attack",
        "defense",
        "special_attack",
        "special_defense",
        "speed",
    }
    assert detail["ivs"] == store.state.active.profile.ivs


def test_computed_stats_reflect_the_individual_not_just_the_species(tmp_path):
    store = _hatched(tmp_path)
    store.state.active.profile.ivs = {key: 31 for key in store.state.active.profile.ivs}
    strong = store.detail_payload(1)["stats"]
    store.state.active.profile.ivs = {key: 0 for key in store.state.active.profile.ivs}
    weak = store.detail_payload(1)["stats"]
    assert all(strong[key] >= weak[key] for key in strong)
    assert strong != weak


def test_the_learnset_comes_through(tmp_path):
    store = _hatched(tmp_path)
    detail = store.detail_payload(1)
    assert detail["moves"] == METADATA["moves"]
    assert detail["version_group"] == "black-white"


# --- gender and ability are derived, once, deterministically -----------------


def test_gender_and_ability_are_filled_in_on_first_view(tmp_path):
    """They need species metadata, which needs the network, so a hatch cannot
    roll them."""
    store = _hatched(tmp_path)
    assert store.state.active.profile.species_id == 0

    detail = store.detail_payload(1)
    assert detail["gender"] in ("male", "female", "genderless")
    assert detail["ability"] in METADATA["abilities"]


def test_the_same_creature_comes_back_every_time(tmp_path):
    """The derivation is seeded from the individual's own IVs, so it survives a
    restart and can be recomputed on any thread without coordination."""
    store = _hatched(tmp_path)
    first = store.detail_payload(1)

    reloaded = CompanionStore(
        save_path=tmp_path / "c.json", api=FakeAPI(), rng=random.Random(99)
    )
    second = reloaded.detail_payload(1)
    assert (first["gender"], first["ability"]) == (second["gender"], second["ability"])


def test_a_hidden_ability_is_never_rolled(tmp_path):
    store = _hatched(tmp_path)
    for _ in range(20):
        store.state.active.profile.species_id = 0
        assert store.detail_payload(1)["ability"] != "solar-power"


def test_viewing_the_page_does_not_write_the_save(tmp_path):
    """The web thread serves this from a throwaway store; a second writer on
    one save file is how progress gets clobbered."""
    store = _hatched(tmp_path)
    save_file = tmp_path / "c.json"
    before = save_file.read_bytes()
    store.detail_payload(1)
    assert save_file.read_bytes() == before


def test_a_revealed_ditto_reports_its_own_identity(tmp_path):
    """The reveal does not rewrite path_ids, so the stored gender and ability
    still belong to the disguise until they are re-derived."""
    store = _hatched(tmp_path)
    store.detail_payload(1)  # settle gender/ability as the disguise
    assert store.state.active.profile.species_id == 1

    store.state.active.ditto_disguise = 1
    store.state.active.ditto_revealed = True
    detail = store.detail_payload(balance.DITTO_SPECIES_ID)
    assert detail["species_id"] == balance.DITTO_SPECIES_ID
    # The IVs are the individual's own and must survive the reveal.
    assert detail["ivs"] == store.state.active.profile.ivs


# --- records of creatures that no longer exist ------------------------------


def test_a_released_individual_keeps_its_page(tmp_path):
    store = _hatched(tmp_path)
    ivs = dict(store.state.active.profile.ivs)
    store.state.used_since_install = balance.FRESH_EGG_PRICE
    store.state.spent_tokens = 0
    shop.buy(store.state, "egg")

    detail = store.detail_payload(1)
    assert detail["ivs"] == ivs
    assert detail["level"] == 1


def test_a_graduated_individual_reads_as_level_one_hundred(tmp_path):
    store = _hatched(tmp_path, api=FakeAPI())
    apply_usage(
        store.state, balance.graduation_total(Rarity.COMMON) * 2, rng=random.Random(4)
    )
    assert store.state.active is None
    assert store.detail_payload(3)["level"] == 100


def test_an_entry_from_before_profiles_says_the_individual_is_unknown(tmp_path):
    """Better than inventing a perfect creature out of missing data."""
    store = _store(tmp_path)
    store.state.dex.append(
        DexEntry(
            base_id=1,
            final_id=1,
            chain_order=[1],
            rarity=Rarity.COMMON,
            caught_at=1.0,
        )
    )
    detail = store.detail_payload(1)
    assert detail["has_individual"] is False
    assert detail["stats"] == {}
    assert detail["types"] == ["grass", "poison"], "species data still renders"


# --- failure ----------------------------------------------------------------


def test_the_page_is_none_when_species_data_cannot_be_had(tmp_path):
    """An offline deployment is an expected state, not a bug; the UI says
    "details unavailable" rather than rendering an empty creature."""
    store = _hatched(tmp_path, api=FakeAPI(fail=True))
    assert store.detail_payload(1) is None


def test_no_api_means_no_page_rather_than_a_crash(tmp_path):
    store = CompanionStore(save_path=tmp_path / "c.json", api=None, rng=random.Random(1))
    assert store.detail_payload(1) is None


def test_a_species_nobody_owns_still_renders_from_species_data(tmp_path):
    store = _hatched(tmp_path)
    detail = store.detail_payload(493)
    assert detail is not None
    assert detail["has_individual"] is False
    assert detail["level"] is None
