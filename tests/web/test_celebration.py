"""The engine tells you a Pokemon hatched. It does not tell you which rarity,
which nature, or -- for a graduation -- how long it took, even though it knows
all three. These tests pin the enrichment that adds them.
"""

from __future__ import annotations

import pytest

from poketokenweb import celebration


def _mon(**over) -> dict:
    base = {
        "stage": "mon",
        "species_id": 501,
        "name": "Oshawott",
        "rarity": "rare",
        "nature": "relaxed",
        "is_shiny": False,
        "stage_index": 0,
        "total_forms": 3,
    }
    base.update(over)
    return base


def _graduation(**over) -> dict:
    entry = {
        "rarity": "legendary",
        "nature": "modest",
        "is_shiny": False,
        "raised_text": "2 days, 4 hr",
        "chain": [
            {"species_id": 501, "name": "Oshawott"},
            {"species_id": 502, "name": "Dewott"},
            {"species_id": 503, "name": "Samurott"},
        ],
    }
    entry.update(over)
    return {"companion": {"stage": "egg"}, "catch_log": [entry]}


# --- hatched ---------------------------------------------------------------

def test_hatched_carries_rarity_and_nature():
    a = celebration.describe(
        {"kind": "hatched", "title": "It hatched!", "detail": "Oshawott came out of the egg."},
        {"companion": _mon()},
    )
    assert a.title == "🐣 It hatched!"
    assert "Oshawott came out of the egg." in a.body
    assert "Rare" in a.body
    assert "relaxed nature" in a.body
    assert a.species_id == 501
    assert a.shiny is False


def test_shiny_is_flagged_and_keeps_the_odds():
    a = celebration.describe(
        {"kind": "shiny", "title": "A shiny hatched!", "detail": "A shiny Oshawott — 1 in 64!"},
        {"companion": _mon(is_shiny=True)},
    )
    assert a.title == "✨ A shiny hatched!"
    assert "1 in 64" in a.body
    assert a.shiny is True


def test_evolved_reports_progress_through_the_line():
    a = celebration.describe(
        {"kind": "evolved", "title": "Evolved!", "detail": "It became Dewott."},
        {"companion": _mon(species_id=502, name="Dewott", stage_index=1)},
    )
    assert a.title == "⚡ Evolved!"
    assert "stage 2 of 3" in a.body
    assert a.species_id == 502


# --- graduated -------------------------------------------------------------

def test_graduated_reads_the_catch_log_not_the_companion():
    """By publish time the companion is already a fresh egg.

    Reading `companion` here would describe the successor and report the
    graduate's rarity and nature as blank.
    """
    a = celebration.describe(
        {"kind": "graduated", "title": "Graduated!", "detail": "Samurott joined your Pokedex."},
        _graduation(),
    )
    assert a.title == "🎓 Graduated!"
    assert "Legendary" in a.body
    assert "modest nature" in a.body
    assert "raised in 2 days, 4 hr" in a.body
    # The final form, not the base species the line started from.
    assert a.species_id == 503


def test_graduated_shiny_is_flagged():
    a = celebration.describe(
        {"kind": "graduated", "title": "Graduated!", "detail": "x"},
        _graduation(is_shiny=True),
    )
    assert "✨ Shiny" in a.body
    assert a.shiny is True


def test_graduated_without_a_catch_log_still_announces():
    a = celebration.describe(
        {"kind": "graduated", "title": "Graduated!", "detail": "It joined your Pokedex."},
        {"companion": {"stage": "egg"}},
    )
    assert a.title == "🎓 Graduated!"
    assert a.body == "It joined your Pokedex."
    assert a.species_id is None


# --- ditto -----------------------------------------------------------------

def test_ditto_says_nothing_extra():
    # The joke is that its line, rarity and nature were never real.
    a = celebration.describe(
        {"kind": "ditto", "title": "Huh? It's Ditto!", "detail": "It was a Ditto all along."},
        {"companion": _mon()},
    )
    assert a.title == "🫠 Huh? It's Ditto!"
    assert a.body == "It was a Ditto all along."
    assert "Rare" not in a.body


# --- robustness ------------------------------------------------------------

@pytest.mark.parametrize("bad", [None, {}, {"kind": ""}, "nope", 42])
def test_nothing_to_announce(bad):
    assert celebration.describe(bad, {"companion": _mon()}) is None


@pytest.mark.parametrize("payload", [None, {}, {"companion": None}, {"companion": "x"}, 7])
def test_a_missing_or_malformed_payload_still_announces(payload):
    a = celebration.describe(
        {"kind": "hatched", "title": "It hatched!", "detail": "Something appeared."}, payload
    )
    assert a is not None
    assert a.body == "Something appeared."
    assert a.species_id is None


def test_absent_facts_are_omitted_rather_than_rendered_empty():
    a = celebration.describe(
        {"kind": "hatched", "title": "It hatched!", "detail": "It appeared."},
        {"companion": {"stage": "mon", "species_id": 1}},
    )
    assert a.body == "It appeared."
    assert " · " not in a.body


def test_a_single_form_line_reports_no_stage_progress():
    a = celebration.describe(
        {"kind": "hatched", "title": "It hatched!", "detail": "Ditto appeared."},
        {"companion": _mon(total_forms=1, stage_index=0)},
    )
    assert "stage" not in a.body


def test_an_unknown_rarity_is_passed_through_titled():
    a = celebration.describe(
        {"kind": "hatched", "title": "t", "detail": "d"},
        {"companion": _mon(rarity="mythical")},
    )
    assert "Mythical" in a.body


def test_every_kind_has_an_emoji():
    for kind in ("hatched", "shiny", "evolved", "graduated", "ditto"):
        a = celebration.describe({"kind": kind, "title": "T", "detail": "D"}, {})
        assert a.title.startswith(celebration.EMOJI[kind])
