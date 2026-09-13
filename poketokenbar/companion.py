"""Companion state and growth — ports CompanionModel/CompanionStore.swift.

Pure functions over CompanionState with no I/O, so the whole game is testable
without a network or a filesystem. Species data arrives through an injected
line provider; persistence lives in save.py.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from . import balance
from . import profile as profile_mod
from .balance import Rarity
from .profile import PokemonProfile


@dataclass(slots=True)
class EvoLine:
    """One evolution line: ordered species ids from base to final."""

    base_id: int
    path_ids: list[int]
    rarity: Rarity
    names: dict[int, dict[str, str]] = field(default_factory=dict)

    @property
    def total_forms(self) -> int:
        return len(self.path_ids)


@dataclass(slots=True)
class MonState:
    base_id: int
    path_ids: list[int]
    planned_path_ids: list[int]
    stage_index: int = 0
    used_at_stage: int = 0
    rarity: Rarity = Rarity.COMMON
    total_forms: int = 1
    is_shiny: bool = False
    nature: str | None = None
    ditto_disguise: int | None = None
    ditto_revealed: bool = False
    hatched_at: float | None = None
    # Decided once, at hatch, and persisted: this individual's line had already
    # been graduated, so it grows at REPEAT_GROWTH_MULTIPLIER. Recomputing it
    # later would change an in-flight companion's cost the moment an unrelated
    # line graduated.
    has_growth_boost: bool = False
    # Rolled once at hatch and persisted, so the same creature stays the same
    # creature. None only on saves written before profiles existed.
    profile: PokemonProfile | None = None

    @property
    def growth_multiplier(self) -> int:
        return balance.REPEAT_GROWTH_MULTIPLIER if self.has_growth_boost else 1

    @property
    def phase_threshold(self) -> int:
        """Tokens this stage needs, before difficulty.

        Read thresholds through here rather than calling balance.phase_threshold
        directly: a call site that forgets the multiplier silently reverts that
        one path to standard growth, and the symptom (a boosted companion that
        evolves at the wrong point) looks like a balance opinion rather than a
        bug.
        """
        return balance.phase_threshold(
            self.rarity,
            self.total_forms,
            self.stage_index,
            growth_multiplier=self.growth_multiplier,
        )

    @property
    def current_id(self) -> int:
        """Species currently displayed.

        Falls back to base_id when path_ids is empty so a damaged save cannot
        crash rendering, which happens on every frame.
        """
        if self.ditto_revealed:
            return balance.DITTO_SPECIES_ID
        if not self.path_ids:
            return self.base_id
        return self.path_ids[min(self.stage_index, len(self.path_ids) - 1)]

    @property
    def is_final_form(self) -> bool:
        return self.stage_index >= len(self.path_ids) - 1


@dataclass(slots=True)
class DexEntry:
    base_id: int
    final_id: int
    chain_order: list[int]
    rarity: Rarity
    is_shiny: bool = False
    nature: str | None = None
    # Epoch seconds. None on entries written before this was tracked; those
    # sort last rather than pretending to be ancient.
    caught_at: float | None = None
    raised_seconds: float | None = None
    # When this individual was let go to buy a fresh egg. None means it
    # graduated -- which is also what every entry written before this field
    # existed means, so no save migration is needed.
    #
    # Graduations and releases share one list because the Pokedex folds owned
    # SPECIES and does not care how a species was obtained. Keeping releases
    # out of it was the one path by which the collection screen could shrink,
    # which contradicts the promise it makes.
    released_at: float | None = None
    # The individual as it was when the record was made. Kept on the entry
    # rather than recomputed, because the creature no longer exists to ask.
    profile: PokemonProfile | None = None
    # The level it finished at: 100 for a graduation, wherever it had got to
    # for a release. Recomputing is impossible once the progress is gone.
    level: int | None = None

    @property
    def is_released(self) -> bool:
        return self.released_at is not None


@dataclass(slots=True)
class CompanionState:
    # Tokens are only counted from install onward.
    install_baseline_set: bool = False
    used_since_install: int = 0
    # Ledger of tokens spent in the shop. Spendable = used_since_install
    # - spent_tokens. The growth meter (used_since_install) never rewinds.
    spent_tokens: int = 0
    # Tokens absorbed by the current egg; resets per egg.
    egg_usage: int = 0
    # Rarity floor a premium egg guarantees. Persisted because the species roll
    # needs the network, which may be unavailable at purchase time.
    egg_tier: Rarity | None = None
    pending_hatch_id: int | None = None
    claimed_today_tokens_by_provider: dict[str, int] | None = None
    last_date: str = ""
    active: MonState | None = None
    dex: list[DexEntry] = field(default_factory=list)
    collected_finals: set[str] = field(default_factory=set)
    language: str = "en"
    inventory: dict[str, int] = field(default_factory=dict)
    candy_grant_tier: dict[str, int] = field(default_factory=dict)
    candy_feature_seeded: bool = False

    @property
    def spendable_tokens(self) -> int:
        return max(0, self.used_since_install - self.spent_tokens)

    def has_collected_final(self, base_id: int) -> bool:
        """Whether this LINE has ever been graduated.

        Keyed on the base species, not the final. A final-based test would leak
        the branch a future hatch is going to take: two lines starting at the
        same base can end at different finals, and asking about the final means
        asking a question the player has not been shown the answer to yet.
        """
        prefix = f"{base_id}-"
        return any(key.startswith(prefix) for key in self.collected_finals)


@dataclass(slots=True)
class GrowthEvents:
    """What happened during one apply_usage call, for notifications."""

    hatched: int | None = None
    evolved_to: int | None = None
    graduated: DexEntry | None = None
    ditto_revealed: bool = False


def display_state(
    state: CompanionState,
    today_tokens: int,
    limit_warning: bool = False,
    just_evolved: bool = False,
) -> str:
    """Which mood the companion is in — ports computeState().

    Order matters: a level-up beats a limit warning, which beats sleep. Any
    other ordering hides the celebration behind a warning.
    """
    if state.active is None:
        return "egg"
    if just_evolved:
        return "levelUp"
    if limit_warning:
        return "tired"
    if today_tokens <= 0:
        return "sleep"
    # Burn tiers, in tokens/day equivalents.
    if today_tokens >= 150_000_000:
        return "focus"
    if today_tokens >= 20_000_000:
        return "working"
    return "idle"


STATUS_MESSAGE = {
    "egg": "An egg is warming up.",
    "idle": "Keeping quiet today.",
    "working": "Today's work is piling up.",
    "focus": "In focus mode now.",
    "tired": "Careful — the limit is close.",
    "sleep": "Sleeping now.",
    "levelUp": "It grew!",
}


def egg_threshold(growth_difficulty: float = 1.0) -> int:
    """Tokens this egg needs to hatch, with difficulty applied."""
    return balance.scaled(balance.EGG_HATCH_THRESHOLD, growth_difficulty)


def stage_threshold(mon: MonState, growth_difficulty: float = 1.0) -> int:
    """Tokens this stage needs, with the repeat bonus AND difficulty applied.

    Every growth decision reads this. Calling balance.phase_threshold or
    MonState.phase_threshold directly from a growth path would silently drop
    difficulty for that path alone, which looks like a balance opinion rather
    than a bug.
    """
    return balance.scaled(mon.phase_threshold, growth_difficulty)


def rescale_banked_growth(state: CompanionState, old: float, new: float) -> None:
    """Keep the SHARE of the current egg/stage already earned when difficulty
    moves.

    These are progression credits, not usage. Lifetime tokens, the per-provider
    ledger and the wallet are never touched here -- someone who makes the game
    easier has not spent more money, and someone who makes it harder has not
    un-spent any.

    Without this, lowering difficulty would instantly complete a stage that was
    most of the way through at the old scale, and raising it would appear to
    delete progress.
    """
    def rescale(credits: int, base: int) -> int:
        old_threshold = max(1, round(base * old))
        new_threshold = max(1, round(base * new))
        if old_threshold == new_threshold or credits <= 0:
            return max(0, credits)
        value = int(credits / old_threshold * new_threshold)
        # Rounding must never turn an incomplete stage into a completed one:
        # that would evolve or graduate a companion because a slider moved.
        if credits < old_threshold:
            return max(0, min(new_threshold - 1, value))
        return max(0, value)

    active = state.active
    if active is not None:
        active.used_at_stage = rescale(active.used_at_stage, active.phase_threshold)
    else:
        state.egg_usage = rescale(state.egg_usage, balance.EGG_HATCH_THRESHOLD)


def roll_shiny(rng: random.Random, has_charm: bool) -> bool:
    denominator = (
        balance.SHINY_CHARM_DENOMINATOR if has_charm else balance.SHINY_DENOMINATOR
    )
    return rng.randrange(denominator) == 0


def roll_nature(rng: random.Random) -> str:
    return rng.choice(balance.NATURES)


def roll_ditto(rng: random.Random, line: EvoLine) -> bool:
    """Whether this hatch is secretly a disguised Ditto.

    Restricted to common lines with 2+ forms, matching the Swift rule: the joke
    only lands when the disguise is something ordinary that visibly "evolves"
    before the reveal.
    """
    if line.rarity != Rarity.COMMON or line.total_forms < 2:
        return False
    return rng.randrange(balance.DITTO_DISGUISE_DENOMINATOR) == 0


def hatch(state: CompanionState, line: EvoLine, rng: random.Random) -> MonState:
    """Turn the egg into a companion. Shiny and nature are fixed here."""
    has_charm = state.inventory.get("shinyCharm", 0) > 0
    mon = MonState(
        base_id=line.base_id,
        path_ids=list(line.path_ids),
        planned_path_ids=list(line.path_ids),
        stage_index=0,
        used_at_stage=0,
        rarity=line.rarity,
        total_forms=line.total_forms,
        is_shiny=roll_shiny(rng, has_charm),
        nature=roll_nature(rng),
        has_growth_boost=state.has_collected_final(line.base_id),
        hatched_at=__import__("time").time(),
        # IVs are pure randomness and are fixed here. Gender and ability need
        # species metadata (a network call), so species_id stays 0 and the
        # display layer derives them on first use -- an offline hatch must
        # still produce a real creature.
        profile=PokemonProfile(ivs=profile_mod.roll_ivs(rng), species_id=0),
        # The disguise stores the species being impersonated; the reveal swaps
        # the display to Ditto while keeping this for the "it was Ditto!" moment.
        ditto_disguise=line.base_id if roll_ditto(rng, line) else None,
    )
    state.active = mon
    # The guarantee is consumed by the hatch it paid for.
    state.egg_tier = None
    state.pending_hatch_id = None
    state.egg_usage = 0
    return mon


def graduate(state: CompanionState, mon: MonState, now: float | None = None) -> DexEntry:
    """Archive a completed companion and clear the slot for a fresh egg."""
    import time as _time

    now = _time.time() if now is None else now
    entry = DexEntry(
        base_id=mon.base_id,
        final_id=mon.current_id,
        chain_order=list(mon.path_ids),
        rarity=mon.rarity,
        is_shiny=mon.is_shiny,
        nature=mon.nature,
        caught_at=now,
        raised_seconds=(now - mon.hatched_at) if mon.hatched_at else None,
        profile=mon.profile,
        level=profile_mod.MAX_LEVEL,
    )
    state.dex.append(entry)
    state.collected_finals.add(f"{mon.base_id}-{mon.current_id}")
    state.active = None
    state.egg_usage = 0
    return entry


def release(
    state: CompanionState,
    mon: MonState,
    now: float | None = None,
    growth_difficulty: float = 1.0,
) -> DexEntry:
    """Record a companion let go to buy a fresh egg, and clear the slot.

    Three rules, each load-bearing:

    * **Only forms it actually reached** (``path_ids[:stage_index + 1]``).
      Crediting the planned path would make buying an egg a shortcut to filling
      the Pokedex with evolutions the creature never became.
    * **``collected_finals`` is untouched.** It was not raised to its final
      form, so it must not count toward completion or shift the hatch weighting.
    * **``caught_at`` is the moment of release**, because the catch log sorts on
      that field and the record came into existence now.

    ``chain_order`` falls back to ``base_id`` for a damaged save whose
    ``stage_index`` is out of range, matching ``MonState.current_id``'s
    attitude: never let one bad field cost the whole entry.
    """
    import time as _time

    now = _time.time() if now is None else now
    reached = list(mon.path_ids[: max(1, mon.stage_index + 1)]) or [mon.base_id]
    entry = DexEntry(
        base_id=mon.base_id,
        final_id=reached[-1],
        chain_order=reached,
        rarity=mon.rarity,
        is_shiny=mon.is_shiny,
        nature=mon.nature,
        caught_at=now,
        raised_seconds=(now - mon.hatched_at) if mon.hatched_at else None,
        released_at=now,
        profile=mon.profile,
        level=profile_mod.level_of(mon, growth_difficulty),
    )
    state.dex.append(entry)
    state.active = None
    return entry


def apply_usage(
    state: CompanionState,
    tokens: int,
    line_for_egg=None,
    rng: random.Random | None = None,
    growth_difficulty: float = 1.0,
) -> GrowthEvents:
    """Feed tokens to the companion.

    Overflow always carries forward, so a single large delta can hatch and then
    immediately advance a stage rather than being clipped.
    """
    events = GrowthEvents()
    if tokens <= 0:
        return events
    rng = rng or random.Random()

    state.used_since_install += tokens

    # --- egg ---
    if state.active is None:
        hatch_at = egg_threshold(growth_difficulty)
        state.egg_usage += tokens
        if state.egg_usage < hatch_at:
            return events
        if line_for_egg is None:
            # No species data (offline). Hold the tokens in the egg and hatch
            # once a line is available — never discard progress.
            return events
        overflow = state.egg_usage - hatch_at
        mon = hatch(state, line_for_egg, rng)
        events.hatched = mon.current_id
        tokens = overflow
        if tokens <= 0:
            return events

    # --- growth ---
    mon = state.active
    mon.used_at_stage += tokens
    while True:
        threshold = stage_threshold(mon, growth_difficulty)
        if mon.used_at_stage < threshold:
            break
        mon.used_at_stage -= threshold
        if mon.is_final_form:
            events.graduated = graduate(state, mon)
            break
        mon.stage_index += 1
        events.evolved_to = mon.current_id
        # A disguised Ditto reveals itself on its first evolution — the moment
        # the "evolution" would have to actually happen.
        if mon.ditto_disguise is not None and not mon.ditto_revealed:
            mon.ditto_revealed = True
            events.ditto_revealed = True

    return events
