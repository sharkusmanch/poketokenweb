"""Releasing a companion keeps its species — upstream #242 and #211.

Buying an egg used to drop the active companion without recording it, so a
species backed only by that companion disappeared from the Pokedex. That was
the single path by which the collection screen could shrink.
"""

from __future__ import annotations

import random

from poketokenbar import balance, shop
from poketokenbar.balance import Rarity
from poketokenbar.companion import EvoLine, apply_usage, graduate
from poketokenbar.companion_store import CompanionStore


class FakeAPI:
    def __init__(self, forms=3, rarity=Rarity.COMMON):
        self.forms, self.rarity = forms, rarity

    def roll_base_species(self, rng, tier=None):
        return 1

    def line(self, base_id):
        return EvoLine(
            base_id=1, path_ids=list(range(1, self.forms + 1)), rarity=self.rarity
        )

    def species(self, species_id):
        return {"names": [{"language": {"name": "en"}, "name": f"Mon{species_id}"}]}


def _store(tmp_path, forms=3):
    store = CompanionStore(
        save_path=tmp_path / "c.json", api=FakeAPI(forms=forms), rng=random.Random(2)
    )
    store.update({}, today="2026-08-19")
    return store


def _hatched(tmp_path, forms=3):
    store = _store(tmp_path, forms=forms)
    store.update({"claude_code": balance.EGG_HATCH_THRESHOLD}, today="2026-08-19")
    return store


# --- the species survives ---------------------------------------------------


def test_the_species_stays_in_the_dex_after_release(tmp_path):
    store = _hatched(tmp_path)
    assert [row["species_id"] for row in store.dex_payload()] == [1]

    store.state.used_since_install = balance.FRESH_EGG_PRICE
    store.state.spent_tokens = 0
    shop.buy(store.state, "egg")

    assert store.state.active is None
    assert [row["species_id"] for row in store.dex_payload()] == [1]


def test_a_released_species_is_no_longer_flagged_as_raising(tmp_path):
    store = _hatched(tmp_path)
    store.state.used_since_install = balance.FRESH_EGG_PRICE
    store.state.spent_tokens = 0
    shop.buy(store.state, "egg")
    assert [row["is_raising"] for row in store.dex_payload()] == [False]


def test_the_catch_log_tells_a_release_from_a_graduation(tmp_path):
    store = _hatched(tmp_path)
    store.state.used_since_install = balance.FRESH_EGG_PRICE
    store.state.spent_tokens = 0
    shop.buy(store.state, "egg")
    rows = store.catch_log_payload()
    assert len(rows) == 1
    assert rows[0]["released"] is True
    assert rows[0]["raising"] is False


def test_a_graduated_entry_is_not_marked_released_in_the_log(tmp_path):
    store = _hatched(tmp_path, forms=1)
    # One form, so the next threshold graduates it outright.
    apply_usage(
        store.state,
        balance.graduation_total(Rarity.COMMON),
        rng=random.Random(3),
    )
    rows = [row for row in store.catch_log_payload() if not row["raising"]]
    assert rows and all(row["released"] is False for row in rows)


# --- the Raising badge (#211) ----------------------------------------------


def test_only_the_current_form_is_flagged_as_raising(tmp_path):
    """The badge says 'this is what you are raising now'. Putting it on every
    earlier form read as raising several Pokemon at once."""
    store = _hatched(tmp_path)
    threshold = balance.phase_threshold(Rarity.COMMON, 3, 0)
    apply_usage(store.state, threshold, rng=random.Random(4))
    assert store.state.active.stage_index == 1

    rows = {row["species_id"]: row["is_raising"] for row in store.dex_payload()}
    assert rows == {1: False, 2: True}


def test_no_badge_at_all_once_nothing_is_being_raised(tmp_path):
    store = _hatched(tmp_path, forms=1)
    apply_usage(
        store.state, balance.graduation_total(Rarity.COMMON), rng=random.Random(5)
    )
    assert store.state.active is None
    assert all(row["is_raising"] is False for row in store.dex_payload())


def test_a_revealed_ditto_enters_the_dex_as_itself(tmp_path):
    """The reveal does not rewrite path_ids, so without an explicit append the
    Ditto you now own is absent from the collection until it graduates."""
    store = _hatched(tmp_path)
    mon = store.state.active
    mon.ditto_disguise = mon.base_id
    mon.ditto_revealed = True

    ids = [row["species_id"] for row in store.dex_payload()]
    assert balance.DITTO_SPECIES_ID in ids
    raising = {row["species_id"]: row["is_raising"] for row in store.dex_payload()}
    assert raising[balance.DITTO_SPECIES_ID] is True


# --- what must NOT change ---------------------------------------------------


def test_releasing_does_not_count_toward_completion(tmp_path):
    """collected_finals drives branch weighting and the repeat-hatch bonus. A
    creature that was let go mid-line was not raised to its final form."""
    store = _hatched(tmp_path)
    store.state.used_since_install = balance.FRESH_EGG_PRICE
    store.state.spent_tokens = 0
    shop.buy(store.state, "egg")
    assert store.state.collected_finals == set()


def test_a_release_survives_a_save_round_trip(tmp_path):
    store = _hatched(tmp_path)
    store.state.used_since_install = balance.FRESH_EGG_PRICE
    store.state.spent_tokens = 0
    shop.buy(store.state, "egg")
    store._persist()

    reloaded = CompanionStore(
        save_path=tmp_path / "c.json", api=FakeAPI(), rng=random.Random(2)
    )
    assert len(reloaded.state.dex) == 1
    assert reloaded.state.dex[0].is_released is True


def test_an_old_save_without_the_field_reads_as_graduated(tmp_path):
    """released_at absent means graduated, which is what every entry written
    before the field existed was — so no migration is needed."""
    from poketokenbar import save

    raw = {
        "dex": [
            {
                "base_id": 1,
                "final_id": 3,
                "chain_order": [1, 2, 3],
                "rarity": "common",
                "caught_at": 1.0,
            }
        ]
    }
    state = save.decode(raw)
    assert len(state.dex) == 1
    assert state.dex[0].is_released is False


# --- a revealed Ditto must not vanish on the way out (review finding) --------


def _revealed_ditto(tmp_path):
    store = _hatched(tmp_path)
    mon = store.state.active
    mon.ditto_disguise = mon.base_id
    mon.ditto_revealed = True
    return store, mon


def test_a_revealed_ditto_stays_in_the_dex_after_release(tmp_path):
    """The reveal does not rewrite path_ids, so the exit records were built
    from the disguise's line alone and the Ditto disappeared the moment it was
    let go -- the one thing the collection screen promises cannot happen."""
    store, _ = _revealed_ditto(tmp_path)
    assert balance.DITTO_SPECIES_ID in [r["species_id"] for r in store.dex_payload()]

    store.state.used_since_install = balance.FRESH_EGG_PRICE
    store.state.spent_tokens = 0
    shop.buy(store.state, "egg")

    assert balance.DITTO_SPECIES_ID in [r["species_id"] for r in store.dex_payload()]


def test_a_revealed_ditto_stays_in_the_dex_after_graduation(tmp_path):
    store, mon = _revealed_ditto(tmp_path)
    mon.stage_index = len(mon.path_ids) - 1
    entry = graduate(store.state, mon)

    assert balance.DITTO_SPECIES_ID in entry.chain_order
    assert balance.DITTO_SPECIES_ID in [r["species_id"] for r in store.dex_payload()]


def test_a_graduated_entrys_final_form_is_always_in_its_own_chain(tmp_path):
    """final_id named a species chain_order did not contain, so anything
    folding the chain lost the creature the record was actually about."""
    store, mon = _revealed_ditto(tmp_path)
    mon.stage_index = len(mon.path_ids) - 1
    entry = graduate(store.state, mon)
    assert entry.final_id in entry.chain_order


def test_the_dex_never_shrinks_across_a_release(tmp_path):
    """The property the whole change exists to establish, asserted directly."""
    store = _hatched(tmp_path)
    apply_usage(store.state, balance.phase_threshold(Rarity.COMMON, 3, 0), rng=random.Random(6))
    before = {row["species_id"] for row in store.dex_payload()}

    store.state.used_since_install = balance.FRESH_EGG_PRICE
    store.state.spent_tokens = 0
    shop.buy(store.state, "egg")

    assert before <= {row["species_id"] for row in store.dex_payload()}
