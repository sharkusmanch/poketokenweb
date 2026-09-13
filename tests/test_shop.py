import random

import pytest

from poketokenbar import balance, shop
from poketokenbar.balance import Rarity
from poketokenbar.companion import CompanionState, EvoLine, apply_usage, graduate, release


def _with_mon(tokens=0):
    s = CompanionState()
    line = EvoLine(base_id=1, path_ids=[1, 2, 3], rarity=Rarity.COMMON)
    apply_usage(s, balance.EGG_HATCH_THRESHOLD, line_for_egg=line, rng=random.Random(1))
    s.used_since_install = tokens
    s.spent_tokens = 0
    return s


# --- shop listing ----------------------------------------------------------


def test_entries_are_sorted_by_price():
    prices = [e.price for e in shop.entries(CompanionState())]
    assert prices == sorted(prices)


def test_shiny_charm_shows_as_owned_once_held():
    s = CompanionState()
    s.inventory["shinyCharm"] = 1
    charm = next(e for e in shop.entries(s) if e.key == "shinyCharm")
    assert charm.owned is True


def test_three_egg_tiers_are_offered():
    keys = {e.key for e in shop.entries(CompanionState())}
    assert "egg" in keys and f"egg:{Rarity.UNCOMMON}" in keys and f"egg:{Rarity.RARE}" in keys


# --- buying ----------------------------------------------------------------


def test_buying_debits_the_wallet_but_not_growth():
    s = _with_mon(tokens=balance.RARE_CANDY_PRICE)
    shop.buy(s, "rareCandy")
    assert s.inventory["rareCandy"] == 1
    assert s.spent_tokens == balance.RARE_CANDY_PRICE
    assert s.used_since_install == balance.RARE_CANDY_PRICE  # growth never rewinds
    assert s.spendable_tokens == 0


def test_cannot_buy_without_funds():
    s = CompanionState()
    with pytest.raises(shop.ShopError):
        shop.buy(s, "rareCandy")


def test_unknown_item_is_rejected():
    with pytest.raises(shop.ShopError):
        shop.buy(CompanionState(), "masterball")


def test_shiny_charm_cannot_be_bought_twice():
    s = _with_mon(tokens=balance.SHINY_CHARM_PRICE * 2)
    shop.buy(s, "shinyCharm")
    with pytest.raises(shop.ShopError):
        shop.buy(s, "shinyCharm")


def test_buying_an_egg_releases_the_companion_into_the_dex():
    """Replaces the old "vanish as if never hatched" contract.

    The species had to survive: buying an egg was the ONLY path by which the
    Pokedex could shrink, and a collection screen that promises to accumulate
    must not lose entries. collected_finals still must not move, or releasing
    would count toward completion and skew future branch weighting.
    """
    s = _with_mon(tokens=balance.FRESH_EGG_PRICE)
    shop.buy(s, "egg")
    assert s.active is None
    assert len(s.dex) == 1
    assert s.dex[0].is_released is True
    assert s.collected_finals == set()
    assert s.egg_usage == 0


def test_a_released_companion_credits_only_the_forms_it_reached():
    """Crediting the planned path would make buying an egg a shortcut to
    filling the Pokedex with evolutions the creature never became."""
    s = _with_mon(tokens=balance.FRESH_EGG_PRICE)
    assert s.active.path_ids == [1, 2, 3]
    assert s.active.stage_index == 0
    shop.buy(s, "egg")
    assert s.dex[0].chain_order == [1]
    assert s.dex[0].final_id == 1


def test_a_graduated_entry_is_not_marked_released():
    s = _with_mon()
    entry = graduate(s, s.active)
    assert entry.is_released is False
    assert entry.released_at is None


def test_releasing_records_how_long_it_was_raised():
    s = _with_mon(tokens=balance.FRESH_EGG_PRICE)
    s.active.hatched_at = 1_000.0
    release(s, s.active, now=1_600.0)
    assert s.dex[0].raised_seconds == pytest.approx(600.0)
    assert s.dex[0].caught_at == pytest.approx(1_600.0)


def test_a_damaged_stage_index_still_yields_one_form():
    """Same attitude as MonState.current_id: one bad field must not cost the
    whole entry."""
    s = _with_mon(tokens=balance.FRESH_EGG_PRICE)
    s.active.stage_index = -5
    release(s, s.active)
    assert s.dex[0].chain_order == [1]


def test_premium_egg_records_its_guarantee():
    s = _with_mon(tokens=balance.egg_price(Rarity.RARE))
    shop.buy(s, f"egg:{Rarity.RARE}")
    assert s.egg_tier == Rarity.RARE


# --- using items -----------------------------------------------------------


def test_candy_grows_the_companion():
    s = _with_mon()
    s.inventory["rareCandy"] = 1
    before = s.active.used_at_stage
    shop.use_item(s, "rareCandy")
    assert s.active.used_at_stage == before + balance.RARE_CANDY_XP
    assert s.inventory["rareCandy"] == 0


def test_candy_cannot_be_used_on_an_egg():
    s = CompanionState()
    s.inventory["rareCandy"] = 1
    with pytest.raises(shop.ShopError):
        shop.use_item(s, "rareCandy")


def test_using_an_item_you_lack_is_rejected():
    with pytest.raises(shop.ShopError):
        shop.use_item(_with_mon(), "rareCandy")


def test_mint_rerolls_the_nature():
    s = _with_mon()
    s.inventory["mint"] = 1
    s.active.nature = "hardy"
    shop.use_item(s, "mint", rng=random.Random(5))
    assert s.active.nature in balance.NATURES
    assert s.inventory["mint"] == 0


# --- candy grants ----------------------------------------------------------


def test_first_run_does_not_pay_out_for_an_already_full_window():
    # Installing while a window sits at 100% must not backfill candy.
    s = CompanionState()
    assert shop.grant_candy(s, {"session": 100.0}) == 0
    assert s.inventory.get("rareCandy", 0) == 0


def test_full_session_window_grants_one_candy():
    s = CompanionState()
    shop.grant_candy(s, {"session": 10.0})  # seeds the feature
    assert shop.grant_candy(s, {"session": 100.0}) == balance.RARE_CANDY_SESSION_GRANT


def test_full_weekly_window_grants_five():
    s = CompanionState()
    shop.grant_candy(s, {"weekly": 10.0})
    assert shop.grant_candy(s, {"weekly": 100.0}) == balance.RARE_CANDY_WEEKLY_GRANT


def test_grant_is_edge_triggered_not_repeated_every_poll():
    s = CompanionState()
    shop.grant_candy(s, {"session": 10.0})
    first = shop.grant_candy(s, {"session": 100.0})
    second = shop.grant_candy(s, {"session": 100.0})
    third = shop.grant_candy(s, {"session": 100.0})
    assert first > 0
    assert second == 0 and third == 0


def test_window_rearms_after_dropping_below_the_warning_line():
    s = CompanionState()
    shop.grant_candy(s, {"session": 10.0})
    shop.grant_candy(s, {"session": 100.0})
    shop.grant_candy(s, {"session": 5.0})  # window reset
    assert shop.grant_candy(s, {"session": 100.0}) > 0


def test_window_key_excludes_volatile_fields():
    # A rolling weekly window reports a new resets_at on every fetch. Keying on
    # it re-fired the notification each refresh in the Swift app.
    assert shop.window_key("weekly") == "limit:weekly"
    assert "resets" not in shop.window_key("weekly")


# --- the egg-stage gate (#261) ---------------------------------------------


def test_eggs_stay_listed_during_the_egg_stage():
    """Dropping the cards read as "the shop does not sell eggs" rather than
    "you cannot buy one right now"."""
    keys = {e.key for e in shop.entries(CompanionState())}
    assert "egg" in keys
    assert f"egg:{Rarity.UNCOMMON}" in keys
    assert f"egg:{Rarity.RARE}" in keys


def test_an_egg_is_not_purchasable_without_a_companion():
    eggs = [e for e in shop.entries(CompanionState()) if e.kind == "egg"]
    assert eggs and all(e.purchasable is False for e in eggs)
    assert all(e.blocked_reason_key == "egg_needs_companion" for e in eggs)


def test_an_egg_is_purchasable_once_something_has_hatched():
    eggs = [e for e in shop.entries(_with_mon()) if e.kind == "egg"]
    assert eggs and all(e.purchasable is True for e in eggs)
    assert all(e.blocked_reason_key == "" for e in eggs)


def test_items_are_never_blocked_by_the_egg_stage():
    items = [e for e in shop.entries(CompanionState()) if e.kind == "item"]
    assert items and all(e.purchasable is True for e in items)


@pytest.mark.parametrize("key", ["egg", f"egg:{Rarity.UNCOMMON}", f"egg:{Rarity.RARE}"])
def test_buying_an_egg_during_the_egg_stage_is_refused(key):
    s = CompanionState()
    s.used_since_install = balance.egg_price(Rarity.RARE) * 10
    with pytest.raises(shop.ShopError):
        shop.buy(s, key)


@pytest.mark.parametrize("key", ["egg", f"egg:{Rarity.UNCOMMON}", f"egg:{Rarity.RARE}"])
def test_a_blocked_egg_purchase_does_not_touch_the_wallet(key):
    """The guard sits before the debit: the purchase would buy nothing at all,
    so taking the money would simply delete it."""
    s = CompanionState()
    s.used_since_install = balance.egg_price(Rarity.RARE) * 10
    before = s.spendable_tokens
    with pytest.raises(shop.ShopError):
        shop.buy(s, key)
    assert s.spendable_tokens == before
    assert s.spent_tokens == 0
    assert s.egg_tier is None
