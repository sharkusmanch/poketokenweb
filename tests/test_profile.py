"""Per-individual identity — upstream #264.

Two Pikachu hatched a month apart must not be interchangeable, and the same
creature must stay the same creature across restarts.
"""

from __future__ import annotations

import random

import pytest

from poketokenbar import balance, companion, profile as profile_mod, save
from poketokenbar.balance import Rarity
from poketokenbar.companion import CompanionState, EvoLine, apply_usage, graduate, hatch
from poketokenbar.profile import PokemonProfile

LINE = EvoLine(base_id=1, path_ids=[1, 2, 3], rarity=Rarity.COMMON)


# --- rolling ----------------------------------------------------------------


def test_a_hatch_rolls_six_individual_values():
    mon = hatch(CompanionState(), LINE, random.Random(7))
    assert set(mon.profile.ivs) == set(profile_mod.STAT_KEYS)
    assert all(0 <= value <= 31 for value in mon.profile.ivs.values())


def test_two_hatches_are_different_creatures():
    first = hatch(CompanionState(), LINE, random.Random(1)).profile.ivs
    second = hatch(CompanionState(), LINE, random.Random(2)).profile.ivs
    assert first != second


def test_an_offline_hatch_still_produces_a_creature():
    """Gender and ability need species metadata, which needs the network. The
    hatch must not wait for it."""
    mon = hatch(CompanionState(), LINE, random.Random(7))
    assert mon.profile.ivs
    assert mon.profile.species_id == 0, "not yet derived for any species"


def test_gender_follows_the_species_rate():
    genderless = profile_mod.roll_gender(random.Random(1), -1)
    assert genderless == "genderless"
    # rate 0 is always male, rate 8 always female.
    assert profile_mod.roll_gender(random.Random(1), 0) == "male"
    assert profile_mod.roll_gender(random.Random(1), 8) == "female"


def test_an_absent_gender_rate_is_genderless_rather_than_a_guess():
    assert profile_mod.roll_gender(random.Random(1), None) == "genderless"


def test_a_species_with_no_abilities_yields_an_empty_one():
    assert profile_mod.roll_ability(random.Random(1), []) == ""


# --- the trust boundary -----------------------------------------------------


def test_an_imported_profile_is_clamped():
    """import_from reads a file the user supplies: an IV of 9999 renders a
    creature no roll can produce, and a negative one underflows the formula."""
    rogue = PokemonProfile(
        ivs={"hp": 9999, "attack": -5, "defense": 31},
        gender="attack-helicopter",
        ability=None,  # type: ignore[arg-type]
        species_id=-3,
    )
    fixed = profile_mod.clamp(rogue)
    assert fixed.ivs["hp"] == 31
    assert fixed.ivs["attack"] == 0
    assert fixed.ivs["defense"] == 31
    assert fixed.ivs["speed"] == 0, "a missing stat fills in rather than vanishing"
    assert fixed.gender == "genderless"
    assert fixed.ability == ""
    assert fixed.species_id == 0


def test_a_clamped_profile_has_every_stat():
    fixed = profile_mod.clamp(PokemonProfile(ivs={}))
    assert set(fixed.ivs) == set(profile_mod.STAT_KEYS)


# --- level ------------------------------------------------------------------


def test_a_fresh_hatch_is_level_one():
    mon = hatch(CompanionState(), LINE, random.Random(7))
    assert profile_mod.level_of(mon) == 1


def test_level_rises_with_progress():
    mon = hatch(CompanionState(), LINE, random.Random(7))
    mon.used_at_stage = mon.phase_threshold // 2
    assert profile_mod.level_of(mon) > 1


def test_a_graduated_record_is_level_one_hundred():
    state = CompanionState()
    mon = hatch(state, LINE, random.Random(7))
    mon.stage_index = 2
    entry = graduate(state, mon)
    assert entry.level == 100


@pytest.mark.parametrize("difficulty", [0.1, 0.5, 1.0, 2.0])
def test_difficulty_does_not_move_the_level(difficulty):
    """Level is computed in STANDARD growth units, so both multipliers cancel.
    Otherwise making the game easier would inflate every creature's level."""
    mon = hatch(CompanionState(), LINE, random.Random(7))
    mon.used_at_stage = round(companion.stage_threshold(mon, difficulty) / 2)
    # Stage 0 of a 3-form line is 1/6 of its total cost, so half of it is 1/12
    # of the line -- the same answer at every difficulty.
    assert profile_mod.level_of(mon, difficulty) == 1 + round(99 / 12)


def test_the_repeat_bonus_does_not_move_the_level():
    standard = hatch(CompanionState(), LINE, random.Random(7))
    boosted_state = CompanionState()
    boosted_state.collected_finals.add("1-3")
    boosted = hatch(boosted_state, LINE, random.Random(7))
    assert boosted.has_growth_boost is True

    standard.used_at_stage = standard.phase_threshold // 2
    boosted.used_at_stage = boosted.phase_threshold // 2
    assert profile_mod.level_of(standard) == profile_mod.level_of(boosted)


def test_level_is_bounded_even_with_impossible_progress():
    mon = hatch(CompanionState(), LINE, random.Random(7))
    mon.used_at_stage = 10**15
    assert 1 <= profile_mod.level_of(mon) <= 100


# --- computed stats ---------------------------------------------------------


BASE = {
    "hp": 35,
    "attack": 55,
    "defense": 40,
    "special_attack": 50,
    "special_defense": 50,
    "speed": 90,
}
PERFECT = {key: 31 for key in profile_mod.STAT_KEYS}


def test_the_stat_formula_matches_the_mainline_games():
    # Pikachu, level 100, perfect IVs, neutral nature.
    stats = profile_mod.computed_stats(PERFECT, BASE, 100, "hardy")
    assert stats["hp"] == ((2 * 35 + 31) * 100) // 100 + 100 + 10
    assert stats["speed"] == ((2 * 90 + 31) * 100) // 100 + 5


def test_a_nature_raises_one_stat_and_lowers_another():
    neutral = profile_mod.computed_stats(PERFECT, BASE, 100, "hardy")
    adamant = profile_mod.computed_stats(PERFECT, BASE, 100, "adamant")
    assert adamant["attack"] > neutral["attack"]
    assert adamant["special_attack"] < neutral["special_attack"]
    assert adamant["speed"] == neutral["speed"]


def test_hp_is_never_touched_by_a_nature():
    for nature in profile_mod.NATURE_MODIFIERS:
        stats = profile_mod.computed_stats(PERFECT, BASE, 50, nature)
        assert stats["hp"] == profile_mod.computed_stats(PERFECT, BASE, 50, "hardy")["hp"]


def test_the_five_neutral_natures_change_nothing():
    neutral = profile_mod.computed_stats(PERFECT, BASE, 100, "hardy")
    for nature in ("hardy", "docile", "serious", "bashful", "quirky"):
        assert profile_mod.computed_stats(PERFECT, BASE, 100, nature) == neutral


def test_better_ivs_make_a_better_creature():
    weak = profile_mod.computed_stats(
        {key: 0 for key in profile_mod.STAT_KEYS}, BASE, 100, "hardy"
    )
    strong = profile_mod.computed_stats(PERFECT, BASE, 100, "hardy")
    assert all(strong[key] > weak[key] for key in profile_mod.STAT_KEYS)


def test_an_unknown_nature_is_treated_as_neutral():
    assert profile_mod.computed_stats(PERFECT, BASE, 100, "nonsense") == (
        profile_mod.computed_stats(PERFECT, BASE, 100, "hardy")
    )
    assert profile_mod.computed_stats(PERFECT, BASE, 100, None) == (
        profile_mod.computed_stats(PERFECT, BASE, 100, "hardy")
    )


def test_missing_base_stats_do_not_crash_the_page():
    stats = profile_mod.computed_stats(PERFECT, {}, 50, "hardy")
    assert set(stats) == set(profile_mod.STAT_KEYS)


# --- persistence ------------------------------------------------------------


def test_a_profile_survives_a_save_round_trip():
    state = CompanionState()
    mon = hatch(state, LINE, random.Random(7))
    mon.profile.gender = "female"
    mon.profile.ability = "static"
    mon.profile.species_id = 25

    reloaded = save.decode(save.encode(state))
    assert reloaded.active.profile.ivs == mon.profile.ivs
    assert reloaded.active.profile.gender == "female"
    assert reloaded.active.profile.ability == "static"
    assert reloaded.active.profile.species_id == 25


def test_a_graduated_entrys_profile_survives_a_round_trip():
    state = CompanionState()
    mon = hatch(state, LINE, random.Random(7))
    mon.stage_index = 2
    graduate(state, mon)
    reloaded = save.decode(save.encode(state))
    assert reloaded.dex[0].profile is not None
    assert reloaded.dex[0].level == 100


def test_a_save_written_before_profiles_still_loads():
    """Decoding is additive: a missing profile is None, not an error, and the
    Pokedex it came with must survive untouched."""
    raw = {
        "active": {
            "base_id": 1,
            "path_ids": [1, 2],
            "planned_path_ids": [1, 2],
            "stage_index": 0,
            "rarity": "common",
            "total_forms": 2,
        },
        "dex": [
            {
                "base_id": 4,
                "final_id": 6,
                "chain_order": [4, 5, 6],
                "rarity": "rare",
            }
        ],
    }
    state = save.decode(raw)
    assert state.active is not None
    assert state.active.profile is None
    assert len(state.dex) == 1
    assert state.dex[0].profile is None
    assert state.dex[0].level is None


def test_the_save_declares_its_schema_version():
    assert save.encode(CompanionState())["schema_version"] == save.SCHEMA_VERSION


def test_a_legacy_save_is_backed_up_once_before_it_is_replaced(tmp_path):
    import json

    target = tmp_path / "companion.json"
    legacy = {"used_since_install": 42, "collected_finals": ["1-2"]}
    target.write_text(json.dumps(legacy), encoding="utf-8")

    state = save.load(target)
    backup = tmp_path / f"companion{save.LEGACY_BACKUP_SUFFIX}"
    assert backup.is_file()
    assert json.loads(backup.read_text()) == legacy
    assert state.used_since_install == 42

    # A second upgrade must not overwrite the original with a migrated copy.
    save.save(state, target)
    save.load(target)
    assert json.loads(backup.read_text()) == legacy


def test_an_already_migrated_save_is_not_backed_up(tmp_path):
    target = tmp_path / "companion.json"
    save.save(CompanionState(), target)
    save.load(target)
    assert not (tmp_path / f"companion{save.LEGACY_BACKUP_SUFFIX}").exists()


def _mon_with_profile(profile: dict) -> dict:
    return {
        "active": {
            "base_id": 1,
            "path_ids": [1],
            "planned_path_ids": [1],
            "stage_index": 0,
            "rarity": "common",
            "total_forms": 1,
            "profile": profile,
        }
    }


def test_an_imported_rogue_profile_is_clamped_on_load():
    rogue = {key: 9999 for key in profile_mod.STAT_KEYS}
    rogue["attack"] = -5
    state = save.decode(
        _mon_with_profile({"ivs": rogue, "gender": "???", "species_id": 1})
    )
    assert state.active.profile.ivs["hp"] == 31
    assert state.active.profile.ivs["attack"] == 0
    assert state.active.profile.gender == "genderless"


def test_a_partly_unreadable_profile_is_unknown_rather_than_all_zero():
    """clamp() backfills a missing stat with 0, so keeping a half-readable
    spread would render a genuine-looking 0-IV creature. Inventing an
    individual is worse than admitting it is unknown, and once invented the
    two are indistinguishable."""
    for broken in (
        {"hp": 31},  # five stats missing
        {**{key: 20 for key in profile_mod.STAT_KEYS}, "speed": "31"},  # one a string
        {**{key: 20 for key in profile_mod.STAT_KEYS}, "speed": 20.5},  # one a float
        {**{key: 20 for key in profile_mod.STAT_KEYS}, "speed": True},  # bool is an int
    ):
        state = save.decode(_mon_with_profile({"ivs": broken, "species_id": 1}))
        assert state.active.profile is None, broken


def test_a_complete_readable_spread_still_loads():
    good = {key: 20 for key in profile_mod.STAT_KEYS}
    state = save.decode(_mon_with_profile({"ivs": good, "species_id": 1}))
    assert state.active.profile is not None
    assert state.active.profile.ivs == good
