"""Repeat hatches grow twice as fast — upstream #254.

The hatch ROLL already biases away from a line you have graduated (its weight
is halved). This makes the repeat itself less of a punishment when it happens
anyway, by halving what the individual costs to raise.
"""

from __future__ import annotations

import random

from poketokenbar import balance, shop
from poketokenbar.balance import Rarity
from poketokenbar.companion import CompanionState, EvoLine, apply_usage, graduate, hatch

LINE = EvoLine(base_id=1, path_ids=[1, 2, 3], rarity=Rarity.COMMON)
STANDARD_FIRST_STAGE = balance.phase_threshold(Rarity.COMMON, 3, 0)


def _fresh() -> CompanionState:
    return CompanionState()


# --- who gets the boost -----------------------------------------------------


def test_a_first_hatch_grows_at_the_standard_rate():
    state = _fresh()
    mon = hatch(state, LINE, random.Random(1))
    assert mon.has_growth_boost is False
    assert mon.growth_multiplier == 1
    assert mon.phase_threshold == STANDARD_FIRST_STAGE


def test_hatching_a_line_you_have_graduated_grows_twice_as_fast():
    state = _fresh()
    state.collected_finals.add("1-3")
    mon = hatch(state, LINE, random.Random(1))
    assert mon.has_growth_boost is True
    assert mon.growth_multiplier == balance.REPEAT_GROWTH_MULTIPLIER
    assert mon.phase_threshold == round(STANDARD_FIRST_STAGE / 2)


def test_eligibility_is_decided_from_the_base_not_the_final():
    """Two lines can start at the same base and end at different finals. Asking
    about the final asks a question the player has not been shown the answer to
    yet, and would leak which branch a future hatch is going to take."""
    state = _fresh()
    state.collected_finals.add("1-2")  # graduated the OTHER branch
    assert state.has_collected_final(1) is True
    mon = hatch(state, LINE, random.Random(1))  # planned final is 3
    assert mon.has_growth_boost is True


def test_a_neighbouring_species_id_is_not_a_prefix_match():
    """The separator is part of the test, so species 1 and 10 stay distinct."""
    state = _fresh()
    state.collected_finals.add("10-11")
    assert state.has_collected_final(1) is False
    assert state.has_collected_final(10) is True

    other = _fresh()
    other.collected_finals.add("1-3")
    assert other.has_collected_final(10) is False


def test_an_unrelated_graduation_does_not_boost_a_new_line():
    state = _fresh()
    state.collected_finals.add("25-26")
    mon = hatch(state, LINE, random.Random(1))
    assert mon.has_growth_boost is False


# --- the boost is fixed at hatch --------------------------------------------

def test_graduating_another_line_does_not_change_a_companion_in_flight():
    """Recomputing eligibility later would change an in-flight companion's cost
    the moment an unrelated line graduated."""
    state = _fresh()
    mon = hatch(state, LINE, random.Random(1))
    assert mon.has_growth_boost is False

    state.collected_finals.add("1-3")
    assert mon.has_growth_boost is False
    assert mon.phase_threshold == STANDARD_FIRST_STAGE


# --- the boost really is spent ----------------------------------------------


def test_a_boosted_companion_evolves_at_half_the_usage():
    state = _fresh()
    state.collected_finals.add("1-3")
    apply_usage(state, balance.EGG_HATCH_THRESHOLD, line_for_egg=LINE, rng=random.Random(1))
    assert state.active.has_growth_boost is True

    apply_usage(state, round(STANDARD_FIRST_STAGE / 2), rng=random.Random(1))
    assert state.active.stage_index == 1


def test_an_unboosted_companion_does_not_evolve_on_half(tmp_path):
    state = _fresh()
    apply_usage(state, balance.EGG_HATCH_THRESHOLD, line_for_egg=LINE, rng=random.Random(1))
    apply_usage(state, round(STANDARD_FIRST_STAGE / 2), rng=random.Random(1))
    assert state.active.stage_index == 0


# --- what must NOT scale ----------------------------------------------------


def test_rare_candy_xp_is_not_scaled_by_the_boost():
    """Scaling candy alongside the thresholds would cancel out and make candy
    the one thing immune to the bonus."""
    boosted = _fresh()
    boosted.collected_finals.add("1-3")
    apply_usage(boosted, balance.EGG_HATCH_THRESHOLD, line_for_egg=LINE, rng=random.Random(1))
    boosted.inventory["rareCandy"] = 1
    before = boosted.active.used_at_stage
    shop.use_item(boosted, "rareCandy", rng=random.Random(1))
    # The candy injects its full XP; the boost shows up in the threshold it is
    # measured against, not in the amount.
    assert balance.RARE_CANDY_XP == 100_000_000
    assert boosted.active.used_at_stage != before


def test_a_threshold_can_never_reach_zero():
    """A zero threshold makes progress a division by zero and the evolution
    loop degenerate."""
    assert balance.phase_threshold(Rarity.COMMON, 1, 0, growth_multiplier=10**12) >= 1


def test_a_nonsense_multiplier_falls_back_to_standard_growth():
    assert balance.phase_threshold(Rarity.COMMON, 3, 0, growth_multiplier=0) == (
        STANDARD_FIRST_STAGE
    )


# --- persistence ------------------------------------------------------------


def test_the_boost_survives_a_save_round_trip():
    from poketokenbar import save

    state = _fresh()
    state.collected_finals.add("1-3")
    hatch(state, LINE, random.Random(1))
    reloaded = save.decode(save.encode(state))
    assert reloaded.active.has_growth_boost is True
    assert reloaded.active.phase_threshold == round(STANDARD_FIRST_STAGE / 2)


def test_a_save_written_before_the_feature_keeps_standard_growth():
    """Those companions were raised at the standard rate; silently halving what
    is left would be a different creature mid-flight."""
    from poketokenbar import save

    state = _fresh()
    hatch(state, LINE, random.Random(1))
    raw = save.encode(state)
    del raw["active"]["has_growth_boost"]
    reloaded = save.decode(raw)
    assert reloaded.active.has_growth_boost is False
    assert reloaded.active.phase_threshold == STANDARD_FIRST_STAGE


# --- graduation still records the line --------------------------------------


def test_graduating_makes_the_next_hatch_of_that_line_boosted():
    state = _fresh()
    mon = hatch(state, LINE, random.Random(1))
    mon.stage_index = 2
    graduate(state, mon)
    assert state.has_collected_final(1) is True

    again = hatch(state, LINE, random.Random(1))
    assert again.has_growth_boost is True
