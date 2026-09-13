"""Per-individual identity — ports PokemonProfile.swift (upstream #264).

Two Pikachu hatched a month apart should not be interchangeable. Everything
here is rolled ONCE, at hatch, and persisted, so the same creature stays the
same creature across restarts: individual values, gender, and ability.

What is NOT persisted: base stats, types and the moveset. Those are properties
of the species, identical for everyone, and immutable — so they are fetched
from PokeAPI at display time and cached on disk rather than copied into every
save entry.

Level is likewise derived, never stored. It is computed from progress expressed
in STANDARD growth units, so that changing difficulty or hatching a boosted
repeat cannot move a creature's level: both multipliers cancel when the current
stage's fraction is converted back to standard units.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from . import balance

# The six stats, in the order every Pokemon game lists them.
STAT_KEYS: tuple[str, ...] = (
    "hp",
    "attack",
    "defense",
    "special_attack",
    "special_defense",
    "speed",
)

IV_MIN = 0
IV_MAX = 31

MIN_LEVEL = 1
MAX_LEVEL = 100

GENDERS: tuple[str, ...] = ("male", "female", "genderless")

# nature -> (stat raised 10%, stat lowered 10%). A neutral nature raises and
# lowers the same stat, which is why five of them have no effect at all.
NATURE_MODIFIERS: dict[str, tuple[str | None, str | None]] = {
    "hardy": (None, None),
    "lonely": ("attack", "defense"),
    "brave": ("attack", "speed"),
    "adamant": ("attack", "special_attack"),
    "naughty": ("attack", "special_defense"),
    "bold": ("defense", "attack"),
    "docile": (None, None),
    "relaxed": ("defense", "speed"),
    "impish": ("defense", "special_attack"),
    "lax": ("defense", "special_defense"),
    "timid": ("speed", "attack"),
    "hasty": ("speed", "defense"),
    "serious": (None, None),
    "jolly": ("speed", "special_attack"),
    "naive": ("speed", "special_defense"),
    "modest": ("special_attack", "attack"),
    "mild": ("special_attack", "defense"),
    "quiet": ("special_attack", "speed"),
    "bashful": (None, None),
    "rash": ("special_attack", "special_defense"),
    "calm": ("special_defense", "attack"),
    "gentle": ("special_defense", "defense"),
    "sassy": ("special_defense", "speed"),
    "careful": ("special_defense", "special_attack"),
    "quirky": (None, None),
}


@dataclass(slots=True)
class PokemonProfile:
    """The part of an individual that is neither species nor progress."""

    ivs: dict[str, int] = field(default_factory=dict)
    gender: str = "genderless"
    ability: str = ""
    # Which species the gender and ability were rolled for. A revealed Ditto is
    # no longer the species it was pretending to be, so those two must be
    # re-derived; the IVs are the individual's own and survive.
    species_id: int = 0

    @property
    def is_complete(self) -> bool:
        return len(self.ivs) == len(STAT_KEYS) and self.species_id > 0


def roll_ivs(rng: random.Random) -> dict[str, int]:
    return {key: rng.randint(IV_MIN, IV_MAX) for key in STAT_KEYS}


def roll_gender(rng: random.Random, gender_rate: int | None) -> str:
    """PokeAPI's gender_rate is eighths-female, or -1 for genderless."""
    if gender_rate is None or gender_rate < 0:
        return "genderless"
    return "female" if rng.randrange(8) < gender_rate else "male"


def roll_ability(rng: random.Random, abilities: list[str]) -> str:
    """Pick a normal ability. Hidden abilities are excluded by the caller.

    Deterministic fallback to "" rather than raising: an offline hatch must
    still produce a creature, and the detail view renders an unknown ability as
    a placeholder rather than an error.
    """
    return rng.choice(abilities) if abilities else ""


def roll(
    rng: random.Random,
    species_id: int,
    gender_rate: int | None = None,
    abilities: list[str] | None = None,
) -> PokemonProfile:
    return PokemonProfile(
        ivs=roll_ivs(rng),
        gender=roll_gender(rng, gender_rate),
        ability=roll_ability(rng, abilities or []),
        species_id=species_id,
    )


def clamp(profile: PokemonProfile) -> PokemonProfile:
    """Bring an imported profile inside its valid ranges.

    `transfer.import_from` reads a file the user supplies, so this is a trust
    boundary: an IV of 9999 would render a creature no roll can produce, and a
    negative one would underflow the stat formula.
    """
    profile.ivs = {
        key: min(IV_MAX, max(IV_MIN, int(profile.ivs.get(key, 0))))
        for key in STAT_KEYS
    }
    if profile.gender not in GENDERS:
        profile.gender = "genderless"
    if not isinstance(profile.ability, str):
        profile.ability = ""
    profile.species_id = max(0, int(profile.species_id or 0))
    return profile


# --- level ------------------------------------------------------------------


def standard_progress(mon, growth_difficulty: float = 1.0) -> float:
    """How far through its line this individual is, as a fraction of 0..1.

    Expressed in STANDARD growth units, which is what makes level stable:
    completed stages contribute their unmodified cost, and the current stage
    contributes its own fraction converted back to standard units. Difficulty
    and the repeat bonus both scale the current threshold, so both cancel.
    """
    total = float(balance.graduation_total(mon.rarity))
    if total <= 0:
        return 0.0

    earned = 0.0
    for index in range(mon.stage_index):
        earned += balance.phase_threshold(mon.rarity, mon.total_forms, index)

    current_standard = balance.phase_threshold(
        mon.rarity, mon.total_forms, mon.stage_index
    )
    # The SAME threshold used_at_stage was banked against -- repeat bonus and
    # difficulty both. Measuring against the unscaled one would let an easier
    # game inflate every creature's level.
    threshold = balance.scaled(mon.phase_threshold, growth_difficulty)
    if threshold > 0:
        fraction = min(1.0, max(0.0, mon.used_at_stage / threshold))
        earned += fraction * current_standard

    return min(1.0, max(0.0, earned / total))


def level_from_progress(fraction: float) -> int:
    """1 at hatch, 100 at graduation."""
    if fraction <= 0:
        return MIN_LEVEL
    if fraction >= 1:
        return MAX_LEVEL
    return min(MAX_LEVEL, max(MIN_LEVEL, MIN_LEVEL + round(fraction * (MAX_LEVEL - MIN_LEVEL))))


def level_of(mon, growth_difficulty: float = 1.0) -> int:
    return level_from_progress(standard_progress(mon, growth_difficulty))


# --- stats ------------------------------------------------------------------


def computed_stats(
    ivs: dict[str, int], base_stats: dict[str, int], level: int, nature: str | None
) -> dict[str, int]:
    """The mainline stat formula, with no effort values.

    HP has its own formula. Shedinja's 1 HP is a special case in the games and
    is deliberately NOT reproduced: this is a token tracker, and a companion
    with 1 HP would read as a rendering bug.
    """
    level = min(MAX_LEVEL, max(MIN_LEVEL, level))
    raised, lowered = NATURE_MODIFIERS.get((nature or "").lower(), (None, None))

    out: dict[str, int] = {}
    for key in STAT_KEYS:
        base = max(0, int(base_stats.get(key, 0)))
        iv = min(IV_MAX, max(IV_MIN, int(ivs.get(key, 0))))
        if key == "hp":
            value = ((2 * base + iv) * level) // 100 + level + 10
        else:
            value = ((2 * base + iv) * level) // 100 + 5
            if key == raised:
                value = int(value * 1.1)
            elif key == lowered:
                value = int(value * 0.9)
        out[key] = value
    return out
