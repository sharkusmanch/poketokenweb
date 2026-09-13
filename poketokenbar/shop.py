"""Shop, bag, and rare-candy grants — ports the economy half of CompanionStore.

Tokens serve two roles at once: an unspendable growth meter
(used_since_install) and a spendable wallet (minus spent_tokens). Buying never
rewinds growth; it only debits the wallet.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import balance, companion
from .balance import Rarity
from .companion import CompanionState


class ShopError(Exception):
    """A purchase that cannot proceed (unknown item, funds, or state)."""


@dataclass(slots=True)
class ShopEntry:
    key: str
    kind: str  # "item" | "egg"
    price: int
    label: str
    owned: bool = False
    # An l10n key naming why this cannot be bought right now, or "" when it
    # can. A KEY rather than a sentence: the engine resolves strings for the
    # configured language at payload time, as it does for status messages.
    blocked_reason_key: str = ""

    @property
    def purchasable(self) -> bool:
        return not self.blocked_reason_key


def entries(state: CompanionState, shop_difficulty: float = 1.0) -> list[ShopEntry]:
    """Everything on sale, cheapest first.

    Prices are scaled at this consumption site rather than in the constant
    table, because graded egg prices derive from a RATIO of graduation_total --
    scaling the table would let the GROWTH slider move shop pricing too.
    """
    def price(base: int) -> int:
        return balance.scaled(base, shop_difficulty)

    out = [
        ShopEntry("rareCandy", "item", price(balance.RARE_CANDY_PRICE), "Rare Candy"),
        ShopEntry("mint", "item", price(balance.MINT_PRICE), "Mint"),
        ShopEntry(
            "shinyCharm",
            "item",
            price(balance.SHINY_CHARM_PRICE),
            "Shiny Charm",
            # Passive and permanent: held, never consumed, bought once.
            owned=state.inventory.get("shinyCharm", 0) > 0,
        ),
    ]
    # A shop egg always means "release the current Pokemon and reroll", so
    # there is nothing to reroll while one is incubating. The cards stay LISTED
    # during the egg stage regardless -- dropping them read as "the shop does
    # not sell eggs" rather than "you cannot buy one right now".
    blocked = "" if state.active is not None else "egg_needs_companion"
    for tier in balance.EGG_SHOP_TIERS:
        key = f"egg:{tier}" if tier else "egg"
        label = {
            None: "Pokemon Egg",
            Rarity.UNCOMMON: "Uncommon Egg",
            Rarity.RARE: "Rare Egg",
        }[tier]
        out.append(
            ShopEntry(
                key,
                "egg",
                price(balance.egg_price(tier)),
                label,
                blocked_reason_key=blocked,
            )
        )
    return sorted(out, key=lambda e: e.price)


def _debit(state: CompanionState, price: int) -> None:
    if state.spendable_tokens < price:
        raise ShopError(
            f"not enough tokens: need {price:,}, have {state.spendable_tokens:,}"
        )
    state.spent_tokens += price


def buy(
    state: CompanionState,
    key: str,
    shop_difficulty: float = 1.0,
    growth_difficulty: float = 1.0,
) -> str:
    """Purchase one shop entry. Returns a short description of what happened.

    The price is recomputed from the SAME function the listing used, so a
    display price and a charged price cannot drift apart.
    """
    entry = next((e for e in entries(state, shop_difficulty) if e.key == key), None)
    if entry is None:
        raise ShopError(f"unknown shop item: {key}")

    if entry.kind == "item":
        if entry.key == "shinyCharm" and entry.owned:
            raise ShopError("shiny charm is already held")
        _debit(state, entry.price)
        state.inventory[entry.key] = state.inventory.get(entry.key, 0) + 1
        return f"bought {entry.label}"

    # Eggs replace the current companion outright.
    tier = {"egg": None, f"egg:{Rarity.UNCOMMON}": Rarity.UNCOMMON,
            f"egg:{Rarity.RARE}": Rarity.RARE}[entry.key]
    # Before the debit, always: a blocked attempt must not be able to take the
    # money. There is no companion to send away, so the purchase would buy
    # nothing at all.
    if state.active is None:
        raise ShopError("an egg needs a hatched companion to send away")
    _debit(state, entry.price)
    # The companion is RELEASED, not graduated. collected_finals stays
    # untouched -- it was not raised to its final form, so it must not count
    # toward completion or shift the hatch weighting -- but the individual is
    # recorded in the dex so the species it contributed does not vanish from
    # the collection. That was the single path by which the Pokedex could
    # shrink, and a screen that promises to only accumulate must not do that.
    companion.release(state, state.active, growth_difficulty=growth_difficulty)
    state.active = None
    state.egg_usage = 0
    state.egg_tier = tier
    state.pending_hatch_id = None
    return f"bought {entry.label}"


def use_item(
    state: CompanionState, key: str, rng=None, growth_difficulty: float = 1.0
) -> str:
    """Consume one held item.

    ``growth_difficulty`` is threaded through because candy is applied via
    apply_usage, which compares against the difficulty-scaled thresholds. The
    candy's own XP is deliberately NOT scaled -- scaling both would cancel out
    and make candy the one thing immune to difficulty.
    """
    held = state.inventory.get(key, 0)
    if held <= 0:
        raise ShopError(f"no {key} in bag")

    if key == "rareCandy":
        if state.active is None:
            raise ShopError("candy needs a hatched companion")
        state.inventory[key] = held - 1
        # Routed through apply_usage so carry-over, evolution, and graduation
        # all behave exactly as they do for real usage.
        companion.apply_usage(
            state,
            balance.RARE_CANDY_XP,
            rng=rng,
            growth_difficulty=growth_difficulty,
            # Growth only. Candy is bought WITH tokens; crediting its XP back
            # to the wallet would refund part of its own price.
            counts_as_earned=False,
        )
        return "used Rare Candy"

    if key == "mint":
        if state.active is None:
            raise ShopError("mint needs a hatched companion")
        import random

        rng = rng or random.Random()
        state.inventory[key] = held - 1
        state.active.nature = companion.roll_nature(rng)
        return f"nature is now {state.active.nature}"

    raise ShopError(f"{key} cannot be used")


# --- rare candy grants -----------------------------------------------------


def window_key(kind: str) -> str:
    """Stable identifier for a limit window.

    Must never include volatile fields such as resets_at. Including them made
    the Swift app re-notify on every refresh, because a rolling weekly window
    reports a new resets_at each time.
    """
    return f"limit:{kind}"


def grant_candy(state: CompanionState, windows: dict[str, float]) -> int:
    """Award candy for maxed limit windows. Edge-triggered.

    `windows` maps kind ("session"/"weekly") to utilization percent.
    Returns how many candies were granted.
    """
    granted = 0
    for kind, utilization in windows.items():
        key = window_key(kind)
        tier = 2 if utilization >= 100 else (1 if utilization >= 80 else 0)
        previous = state.candy_grant_tier.get(key, 0)

        if tier == 0:
            # Dropped back below the warning line — rearm for next time.
            state.candy_grant_tier.pop(key, None)
            continue
        if tier <= previous:
            continue
        state.candy_grant_tier[key] = tier
        if tier < 2:
            continue  # only a full window pays out

        if not state.candy_feature_seeded:
            # First run must not pay out for a window that was already full
            # before the feature existed.
            continue
        amount = (
            balance.RARE_CANDY_WEEKLY_GRANT
            if kind == "weekly"
            else balance.RARE_CANDY_SESSION_GRANT
        )
        state.inventory["rareCandy"] = state.inventory.get("rareCandy", 0) + amount
        granted += amount

    state.candy_feature_seeded = True
    return granted
