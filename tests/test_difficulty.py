"""Difficulty multipliers — upstream #244, narrowed and fixed by #287.

Two independent, opt-in multipliers. Both default to 1.0, so an existing
install sees no change until someone touches them.
"""

from __future__ import annotations

import random

import pytest

from poketokenbar import balance, companion, shop
from poketokenbar.balance import Rarity
from poketokenbar.companion import CompanionState, EvoLine, apply_usage
from poketokenbar.companion_store import CompanionStore

LINE = EvoLine(base_id=1, path_ids=[1, 2, 3], rarity=Rarity.COMMON)


class FakeAPI:
    def roll_base_species(self, rng, tier=None):
        return 1

    def line(self, base_id):
        return LINE

    def species(self, species_id):
        return {"names": [{"language": {"name": "en"}, "name": f"Mon{species_id}"}]}


def _store(tmp_path, **kwargs) -> CompanionStore:
    store = CompanionStore(
        save_path=tmp_path / "c.json", api=FakeAPI(), rng=random.Random(2), **kwargs
    )
    store.update({}, today="2026-09-13")
    return store


# --- clamping ---------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        (1.0, 1.0),
        (0.5, 0.5),
        (2.0, 2.0),
        (0.1, 0.1),
        (5.0, 2.0),  # above the range
        (0.0, 0.1),  # a zero multiplier would make thresholds zero
        (-3.0, 0.1),
        # Non-finite is nonsense input, not "as extreme as possible": falling
        # back to the default is defensible, silently picking an end of the
        # range is not. Matches upstream's isFinite guard.
        (float("nan"), 1.0),
        (float("inf"), 1.0),
        (float("-inf"), 1.0),
        ("nonsense", 1.0),
        (None, 1.0),
    ],
)
def test_a_usable_multiplier_comes_out_whatever_goes_in(value, expected):
    """config.json is writable from outside the app, so 0, a negative, a string
    and NaN can all genuinely arrive."""
    assert balance.clamp_difficulty(value) == pytest.approx(expected)


def test_a_scaled_constant_is_never_zero():
    assert balance.scaled(1, 0.1) >= 1
    assert balance.scaled(0, 1.0) >= 1


# --- growth -----------------------------------------------------------------


def test_the_default_changes_nothing():
    assert companion.egg_threshold(1.0) == balance.EGG_HATCH_THRESHOLD
    mon = companion.hatch(CompanionState(), LINE, random.Random(1))
    assert companion.stage_threshold(mon, 1.0) == mon.phase_threshold


def test_growth_difficulty_scales_the_egg_threshold():
    assert companion.egg_threshold(0.5) == balance.EGG_HATCH_THRESHOLD // 2
    assert companion.egg_threshold(2.0) == balance.EGG_HATCH_THRESHOLD * 2


def test_growth_difficulty_scales_the_stage_threshold():
    mon = companion.hatch(CompanionState(), LINE, random.Random(1))
    standard = mon.phase_threshold
    assert companion.stage_threshold(mon, 0.5) == round(standard * 0.5)
    assert companion.stage_threshold(mon, 2.0) == round(standard * 2.0)


def test_an_easier_egg_hatches_on_less_usage():
    state = CompanionState()
    apply_usage(
        state,
        balance.EGG_HATCH_THRESHOLD // 2,
        line_for_egg=LINE,
        rng=random.Random(1),
        growth_difficulty=0.5,
    )
    assert state.active is not None


def test_the_same_usage_does_not_hatch_at_standard_difficulty():
    state = CompanionState()
    apply_usage(
        state, balance.EGG_HATCH_THRESHOLD // 2, line_for_egg=LINE, rng=random.Random(1)
    )
    assert state.active is None


def test_growth_difficulty_composes_with_the_repeat_bonus():
    """#244 and #254 multiply. Picking one threshold when integrating them makes
    the other multiplier silently vanish."""
    state = CompanionState()
    state.collected_finals.add("1-3")
    mon = companion.hatch(state, LINE, random.Random(1))
    standard = balance.phase_threshold(Rarity.COMMON, 3, 0)
    assert companion.stage_threshold(mon, 0.5) == round(round(standard / 2) * 0.5)


# --- shop -------------------------------------------------------------------


def test_shop_difficulty_scales_prices():
    cheap = {e.key: e.price for e in shop.entries(CompanionState(), 0.5)}
    standard = {e.key: e.price for e in shop.entries(CompanionState(), 1.0)}
    assert cheap["rareCandy"] == round(standard["rareCandy"] * 0.5)


def test_the_graded_egg_price_ratio_survives_scaling():
    """Egg prices derive from a RATIO of graduation_total (1 : 2.5 : 4). If the
    tables were scaled in place instead of the consumption site, the GROWTH
    slider would move shop pricing as a side effect."""
    for difficulty in (0.1, 1.0, 2.0):
        prices = {e.key: e.price for e in shop.entries(CompanionState(), difficulty)}
        base = prices["egg"]
        assert prices[f"egg:{Rarity.UNCOMMON}"] == pytest.approx(base * 2.5, rel=1e-6)
        assert prices[f"egg:{Rarity.RARE}"] == pytest.approx(base * 4.0, rel=1e-6)


def test_a_purchase_is_charged_the_price_that_was_displayed():
    state = CompanionState()
    listed = {e.key: e.price for e in shop.entries(state, 0.5)}
    state.used_since_install = listed["rareCandy"]
    shop.buy(state, "rareCandy", shop_difficulty=0.5)
    assert state.spent_tokens == listed["rareCandy"]


def test_the_two_multipliers_are_independent(tmp_path):
    lenient_growth = _store(tmp_path / "a", growth_difficulty=0.1, shop_difficulty=1.0)
    standard = _store(tmp_path / "b")
    assert [e.price for e in shop.entries(lenient_growth.state, lenient_growth.shop_difficulty)] == [
        e.price for e in shop.entries(standard.state, standard.shop_difficulty)
    ]


def test_candy_xp_is_not_scaled_but_its_threshold_is(tmp_path):
    """Scaling both would cancel out and make candy the one thing immune."""
    state = CompanionState()
    apply_usage(state, balance.EGG_HATCH_THRESHOLD, line_for_egg=LINE, rng=random.Random(1))
    state.inventory["rareCandy"] = 1
    before = state.active.used_at_stage
    shop.use_item(state, "rareCandy", rng=random.Random(1), growth_difficulty=2.0)
    # Full XP injected; only the bar it is measured against moved.
    assert state.active.used_at_stage - before == balance.RARE_CANDY_XP


# --- rescaling banked progress (#287) ---------------------------------------


def test_lowering_difficulty_keeps_the_share_already_earned(tmp_path):
    store = _store(tmp_path)
    store.state.egg_usage = balance.EGG_HATCH_THRESHOLD // 2  # half way
    store.set_growth_difficulty(0.5)
    assert store.state.egg_usage == pytest.approx(
        companion.egg_threshold(0.5) // 2, rel=1e-6
    )


def test_raising_difficulty_keeps_the_share_already_earned(tmp_path):
    store = _store(tmp_path)
    store.state.egg_usage = balance.EGG_HATCH_THRESHOLD // 4
    store.set_growth_difficulty(2.0)
    assert store.state.egg_usage == pytest.approx(
        companion.egg_threshold(2.0) // 4, rel=1e-6
    )


def test_rescaling_never_completes_a_stage(tmp_path):
    """Rounding must not evolve a companion because a slider moved."""
    store = _store(tmp_path)
    store.state.egg_usage = balance.EGG_HATCH_THRESHOLD - 1  # all but done
    store.set_growth_difficulty(0.1)
    assert store.state.egg_usage < companion.egg_threshold(0.1)


def test_changing_difficulty_does_not_hatch(tmp_path):
    store = _store(tmp_path)
    store.state.egg_usage = balance.EGG_HATCH_THRESHOLD - 1
    store.set_growth_difficulty(0.1)
    assert store.state.active is None, "saving a setting must not advance the game"


def test_changing_difficulty_does_not_evolve(tmp_path):
    store = _store(tmp_path)
    store.update({"claude_code": balance.EGG_HATCH_THRESHOLD}, today="2026-09-13")
    mon = store.state.active
    mon.used_at_stage = mon.phase_threshold - 1
    store.set_growth_difficulty(0.1)
    assert store.state.active.stage_index == 0


def test_rescaling_never_touches_money_or_lifetime_usage(tmp_path):
    store = _store(tmp_path)
    store.state.used_since_install = 900_000_000
    store.state.spent_tokens = 100_000_000
    store.state.claimed_today_tokens_by_provider = {"claude_code": 5}
    store.set_growth_difficulty(0.25)
    assert store.state.used_since_install == 900_000_000
    assert store.state.spent_tokens == 100_000_000
    assert store.state.claimed_today_tokens_by_provider == {"claude_code": 5}


def test_a_no_op_change_leaves_progress_exactly_alone(tmp_path):
    store = _store(tmp_path)
    store.state.egg_usage = 1_234_567
    store.set_growth_difficulty(store.growth_difficulty)
    assert store.state.egg_usage == 1_234_567


def test_an_active_companions_stage_progress_is_rescaled(tmp_path):
    store = _store(tmp_path)
    store.update({"claude_code": balance.EGG_HATCH_THRESHOLD}, today="2026-09-13")
    mon = store.state.active
    mon.used_at_stage = mon.phase_threshold // 2
    store.set_growth_difficulty(0.5)
    assert store.state.active.used_at_stage == pytest.approx(
        store.stage_threshold(store.state.active) // 2, rel=1e-6
    )


def test_shop_difficulty_needs_no_rescale(tmp_path):
    store = _store(tmp_path)
    store.state.egg_usage = 1_000_000
    store.set_shop_difficulty(0.5)
    assert store.state.egg_usage == 1_000_000
    assert store.shop_difficulty == 0.5


def test_the_store_clamps_what_it_is_constructed_with(tmp_path):
    store = _store(tmp_path, growth_difficulty=99.0, shop_difficulty=-1.0)
    assert store.growth_difficulty == balance.DIFFICULTY_MAX
    assert store.shop_difficulty == balance.DIFFICULTY_MIN


# --- persistence as a preference --------------------------------------------


def test_difficulty_is_not_written_into_the_save(tmp_path):
    """It is a preference, not progress. In the save it would travel with an
    export and silently change the importer's difficulty."""
    from poketokenbar import save

    store = _store(tmp_path, growth_difficulty=0.5)
    encoded = save.encode(store.state)
    assert "growth_difficulty" not in encoded
    assert "shop_difficulty" not in encoded
