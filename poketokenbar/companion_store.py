"""Ties the companion engine to live usage — ports CompanionStore.swift.

Providers report cumulative totals for *today*, not deltas. This converts them
into deltas by remembering what has already been credited per provider, which
is why the baseline is tracked per provider id rather than in aggregate: a
single total cannot be decomposed when one provider resets and another does not.
"""

from __future__ import annotations

import random
from datetime import date as _date
from pathlib import Path

from . import balance, companion, l10n, pokeapi, profile as profile_mod, save, shop, sprites
from .companion import CompanionState
from .format import compact as _compact


def _duration(seconds: float | None) -> str:
    if not seconds or seconds <= 0:
        return ""
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    if days > 0:
        return f"{days} days, {hours} hr"
    minutes = int((seconds % 3600) // 60)
    return f"{hours} hr, {minutes} min" if hours else f"{minutes} min"


class CompanionStore:
    def __init__(
        self,
        save_path: Path | None = None,
        api: pokeapi.PokeAPI | None = None,
        sprite_store: sprites.SpriteStore | None = None,
        rng: random.Random | None = None,
        growth_difficulty: float = balance.DEFAULT_DIFFICULTY,
        shop_difficulty: float = balance.DEFAULT_DIFFICULTY,
        read_only: bool = False,
    ) -> None:
        self.save_path = save_path
        # read_only makes "this store never touches the save" structural rather
        # than a convention someone has to keep. The web thread builds one per
        # request; only the poll thread owns the file.
        self.read_only = read_only
        self.state: CompanionState = save.load(save_path, mutate=not read_only)
        self.api = api
        self.sprites = sprite_store
        self.rng = rng or random.Random()
        # Preferences, not save data: they live in config.json alongside the
        # other settings, so importing someone else's save cannot silently
        # change your difficulty, and SaveTransfer stays untouched.
        self.growth_difficulty = balance.clamp_difficulty(growth_difficulty)
        self.shop_difficulty = balance.clamp_difficulty(shop_difficulty)
        # Held for one poll so the popup can show a celebration banner; the
        # notification fires immediately but the banner needs a render pass.
        self.celebration: dict | None = None
        self.last_events: companion.GrowthEvents | None = None

    # --- difficulty ---------------------------------------------------------

    def set_growth_difficulty(self, value) -> None:
        """Apply a new growth multiplier, preserving the share already earned.

        Deliberately does NOT hatch, evolve or graduate. Saving a setting must
        not advance the game: a slider is not usage, and a companion that
        levelled up because someone opened Settings would be indistinguishable
        from one that levelled up because they worked.
        """
        clamped = balance.clamp_difficulty(value)
        if clamped == self.growth_difficulty:
            return
        companion.rescale_banked_growth(self.state, self.growth_difficulty, clamped)
        self.growth_difficulty = clamped
        self._persist()

    def set_shop_difficulty(self, value) -> None:
        """Apply a new price multiplier. Prices are derived, so nothing to fix."""
        self.shop_difficulty = balance.clamp_difficulty(value)

    def egg_threshold(self) -> int:
        return companion.egg_threshold(self.growth_difficulty)

    def stage_threshold(self, mon) -> int:
        return companion.stage_threshold(mon, self.growth_difficulty)

    # --- usage -------------------------------------------------------------

    def update(self, totals_by_provider: dict[str, int], today: str | None = None) -> None:
        """Credit the growth of today's usage since the last update."""
        today = today or _date.today().strftime("%Y-%m-%d")

        # The None sentinel must be checked BEFORE the day rollover, or a
        # fresh save (last_date == "") takes the rollover branch, loses the
        # sentinel, and credits the whole existing day retroactively.
        if self.state.claimed_today_tokens_by_provider is None:
            # First run: seed the baseline, granting nothing for past usage.
            self.state.claimed_today_tokens_by_provider = dict(totals_by_provider)
            self.state.install_baseline_set = True
            self.state.last_date = today
            self._persist()
            return

        # A new day restarts every provider's "today" total at zero, so the
        # old baselines would make every delta negative. Clearing them lets the
        # new day's usage count from zero, which is real usage, not a re-count.
        if self.state.last_date != today:
            self.state.last_date = today
            self.state.claimed_today_tokens_by_provider = {}

        claimed = self.state.claimed_today_tokens_by_provider

        delta = 0
        for provider_id, total in totals_by_provider.items():
            previous = claimed.get(provider_id, 0)
            # A total going backwards (log rotation, cache rebuild) must not
            # produce a negative delta.
            if total > previous:
                delta += total - previous
            claimed[provider_id] = total

        if delta <= 0:
            self._persist()
            return

        line = self._line_for_egg() if self.state.active is None else None
        self.last_events = companion.apply_usage(
            self.state,
            delta,
            line_for_egg=line,
            rng=self.rng,
            growth_difficulty=self.growth_difficulty,
        )
        self._note_celebration(self.last_events)
        self._persist()

    def _note_celebration(self, events) -> None:
        if events is None:
            return
        mon = self.state.active
        name = self.species_name(mon.current_id, self.state.language) if mon else ""
        if events.ditto_revealed:
            self.celebration = {
                "kind": "ditto",
                "title": "Huh? It's Ditto!",
                "detail": "Your companion was a Ditto all along.",
            }
        elif events.graduated is not None:
            self.celebration = {
                "kind": "graduated",
                "title": "Graduated!",
                "detail": f"{name or 'It'} joined your Pokedex.",
            }
        elif events.evolved_to is not None:
            self.celebration = {
                "kind": "evolved",
                "title": "Evolved!",
                "detail": f"It became {name}." if name else "It evolved.",
            }
        elif events.hatched is not None:
            shiny = mon is not None and mon.is_shiny
            self.celebration = {
                "kind": "shiny" if shiny else "hatched",
                "title": "A shiny hatched!" if shiny else "It hatched!",
                "detail": (
                    f"A shiny {name} — 1 in {balance.SHINY_DENOMINATOR}!"
                    if shiny
                    else f"{name} came out of the egg."
                ),
            }

    def _line_for_egg(self):
        """Species data for a hatch, or None when offline."""
        if self.api is None:
            return None
        try:
            species_id = self.state.pending_hatch_id
            if species_id is None:
                species_id = self.api.roll_base_species(self.rng, self.state.egg_tier)
            return self.api.line(species_id)
        except pokeapi.PokeAPIError:
            return None  # hold progress in the egg; hatch on a later poll

    # --- presentation ------------------------------------------------------

    def species_name(self, species_id: int, language: str = "en") -> str:
        """Localised species name, or "" when unknown.

        Reads the on-disk species cache the line lookup already populated, so
        this costs nothing after the hatch and stays silent when offline.
        """
        if self.api is None:
            return ""
        try:
            entry = self.api.species(species_id)
        except Exception:
            return ""
        names = {
            n["language"]["name"]: n["name"]
            for n in entry.get("names", [])
            if n.get("language", {}).get("name")
        }
        # ja-Hrkt is the kana form PokeAPI uses for Japanese.
        for code in ({"ja": ["ja-Hrkt", "ja"]}.get(language, [language])):
            if names.get(code):
                return names[code]
        return names.get("en", "")

    def sprite_path(self) -> str:
        mon = self.state.active
        if mon is None or self.sprites is None:
            return ""
        path = self.sprites.path(mon.current_id, animated=True, shiny=mon.is_shiny)
        return str(path) if path else ""

    def payload(self, today_tokens: int = 0, limit_warning: bool = False) -> dict:
        """Companion section of state.json."""
        kind = companion.display_state(self.state, today_tokens, limit_warning)
        mon = self.state.active
        if mon is None:
            hatch_at = self.egg_threshold()
            progress = min(1.0, self.state.egg_usage / hatch_at)
            return {
                "stage": "egg",
                "label": f"\N{EGG}{round(progress * 100)}%",
                "egg_usage": self.state.egg_usage,
                "egg_progress": round(progress, 4),
                "egg_tier": str(self.state.egg_tier) if self.state.egg_tier else None,
                "sprite_path": "",
                "dex_count": len(self.state.dex),
                "spendable_tokens": self.state.spendable_tokens,
                "spendable_text": _compact(self.state.spendable_tokens),
                "display_state": kind,
                "status_message": l10n.t(f"status_{kind.lower()}", self.state.language),
            }

        # Never balance.phase_threshold directly -- this accessor is the one
        # place the repeat bonus AND difficulty are both applied.
        threshold = self.stage_threshold(mon)
        # Remaining to the NEXT step: an evolution mid-line, graduation at the end.
        remaining = max(0, threshold - mon.used_at_stage)
        evo_line = []
        if self.sprites is not None:
            for index, species_id in enumerate(mon.path_ids):
                path = self.sprites.path(species_id, animated=False, shiny=mon.is_shiny)
                evo_line.append(
                    {
                        "species_id": species_id,
                        "name": self.species_name(species_id, self.state.language),
                        "sprite_path": str(path) if path else "",
                        "current": index == mon.stage_index,
                        "reached": index <= mon.stage_index,
                    }
                )
        return {
            "stage": "mon",
            "label": "",
            "species_id": mon.current_id,
            "name": self.species_name(mon.current_id, self.state.language),
            "is_final_form": mon.is_final_form,
            "remaining_tokens": remaining,
            "remaining_text": _compact(remaining),
            "goal": "graduation" if mon.is_final_form else "next evolution",
            "evo_line": evo_line,
            "is_shiny": mon.is_shiny,
            "nature": mon.nature,
            # None rather than 1 when unboosted, so the UI can test presence
            # instead of comparing against the default.
            "growth_multiplier": mon.growth_multiplier if mon.has_growth_boost else None,
            "level": profile_mod.level_of(mon, self.growth_difficulty),
            "rarity": str(mon.rarity),
            "stage_index": mon.stage_index,
            "total_forms": mon.total_forms,
            "used_at_stage": mon.used_at_stage,
            "stage_threshold": threshold,
            "stage_progress": round(min(1.0, mon.used_at_stage / threshold), 4)
            if threshold
            else 0.0,
            "sprite_path": self.sprite_path(),
            "dex_count": len(self.state.dex),
            "spendable_tokens": self.state.spendable_tokens,
            "spendable_text": _compact(self.state.spendable_tokens),
            "display_state": kind,
            "status_message": l10n.t(f"status_{kind.lower()}", self.state.language),
        }

    # --- economy -----------------------------------------------------------

    def grant_candy(self, windows: dict[str, float]) -> int:
        granted = shop.grant_candy(self.state, windows)
        self._persist()
        return granted

    def buy(self, key: str) -> str:
        message = shop.buy(
            self.state,
            key,
            shop_difficulty=self.shop_difficulty,
            growth_difficulty=self.growth_difficulty,
        )
        self._persist()
        return message

    def use_item(self, key: str) -> str:
        message = shop.use_item(
            self.state,
            key,
            rng=self.rng,
            growth_difficulty=self.growth_difficulty,
        )
        self._persist()
        return message

    def _item_sprite(self, key: str) -> str:
        name = balance.ITEM_SPRITE.get(key)
        if not name or self.sprites is None:
            return ""
        path = self.sprites.item_path(name)
        return str(path) if path else ""

    def shop_payload(self) -> list[dict]:
        spendable = self.state.spendable_tokens
        out = []
        for e in shop.entries(self.state, self.shop_difficulty):
            if e.kind == "item":
                sprite = self._item_sprite(e.key)
                description = balance.ITEM_DESCRIPTION.get(e.key, "")
                badge = ""
            else:
                sprite = self._item_sprite("egg")
                tier = e.key.split(":")[1] if ":" in e.key else None
                description = balance.EGG_DESCRIPTION.get(tier, "")
                badge = (tier or "").upper()
            out.append(
                {
                    "key": e.key,
                    "kind": e.kind,
                    "price": e.price,
                    "price_text": _compact(e.price),
                    "label": e.label,
                    "description": description,
                    "badge": badge,
                    "sprite_path": sprite,
                    "emoji": {"rareCandy": "\N{CANDY}", "mint": "\N{HERB}",
                              "shinyCharm": "\N{SPARKLES}"}.get(e.key, "\N{EGG}"),
                    "owned": e.owned,
                    "owned_count": self.state.inventory.get(e.key, 0),
                    "affordable": spendable >= e.price and not e.owned,
                    # Separate from affordability: "you cannot afford this" and
                    # "this cannot be bought right now" are different answers,
                    # and the state reason takes priority in the UI.
                    "purchasable": e.purchasable,
                    "blocked_reason": (
                        l10n.t(e.blocked_reason_key, self.state.language)
                        if e.blocked_reason_key
                        else ""
                    ),
                }
            )
        return out

    def bag_payload(self) -> list[dict]:
        emoji = {"rareCandy": "\N{CANDY}", "mint": "\N{HERB}", "shinyCharm": "\N{SPARKLES}"}
        return [
            {
                "key": key,
                "label": balance.ITEM_LABEL.get(key, key),
                "description": balance.ITEM_DESCRIPTION.get(key, ""),
                "effect": balance.ITEM_EFFECT.get(key, ""),
                "sprite_path": self._item_sprite(key),
                "emoji": emoji.get(key, "?"),
                "count": count,
                # Passive items are held, not consumed.
                "usable": key in ("rareCandy", "mint") and self.state.active is not None,
                "passive": key == "shinyCharm",
            }
            for key, count in sorted(self.state.inventory.items())
            if count > 0
        ]

    def _owned_species_of_active(self) -> list[int]:
        """Species the current companion has actually been.

        One shared rule with the dex records it will eventually become, so a
        species cannot be present while raising and absent afterwards.
        """
        mon = self.state.active
        return companion.reached_species(mon) if mon is not None else []

    # --- per-individual detail (#264) ---------------------------------------

    def _species_metadata(self, species_id: int) -> dict | None:
        """Immutable species data, or None when it cannot be had right now.

        Best effort by design: the detail page says so rather than rendering an
        empty creature, and nothing else in the app depends on it.
        """
        if self.api is None:
            return None
        try:
            return self.api.metadata(species_id)
        except Exception:
            return None

    @staticmethod
    def _profile_seed(profile, species_id: int) -> int:
        """A stable seed from the individual's own IVs.

        Derived rather than random so that deriving gender and ability twice --
        before and after a restart, or after a failed metadata fetch -- cannot
        produce two different creatures. Built from ints only: str hashing is
        salted per process and would not survive a restart.
        """
        seed = species_id
        for key in profile_mod.STAT_KEYS:
            seed = seed * 32 + int(profile.ivs.get(key, 0))
        return seed

    def _reconcile_profile(self, profile, identity_id: int) -> bool:
        """Derive gender and ability for what this creature actually IS.

        ``identity_id`` is the individual's OWN species -- the active
        companion's current form, or the form a record ended at -- never the
        page being viewed. Keying on the viewed species instead made one
        creature report a different gender and ability on each form of its own
        evolution line, which is not a thing that happens.

        Runs when they were never rolled (an offline hatch leaves species_id 0)
        or when the identity genuinely changed, which is exactly what a
        revealed Ditto is. The IVs are the individual's own and never change.
        """
        if profile is None or identity_id <= 0 or profile.species_id == identity_id:
            return False
        metadata = self._species_metadata(identity_id)
        if metadata is None:
            return False
        rng = random.Random(self._profile_seed(profile, identity_id))
        profile.gender = profile_mod.roll_gender(rng, metadata.get("gender_rate"))
        profile.ability = profile_mod.roll_ability(rng, metadata.get("abilities") or [])
        profile.species_id = identity_id
        return True

    def _individual_for(self, species_id: int):
        """The creature to describe: the active companion, else the newest
        record that reached this species.

        Returns (profile, level, nature, is_shiny, identity_id). ``identity_id``
        is what the creature IS -- its current form, or the form a record ended
        at -- as opposed to the form being viewed. Entries are None where the
        record predates the field.
        """
        mon = self.state.active
        if mon is not None and species_id in companion.reached_species(mon):
            return (
                mon.profile,
                profile_mod.level_of(mon, self.growth_difficulty),
                mon.nature,
                mon.is_shiny,
                mon.current_id,
            )

        for entry in sorted(
            self.state.dex, key=lambda e: e.caught_at or 0, reverse=True
        ):
            if species_id in entry.chain_order:
                return (
                    entry.profile,
                    entry.level,
                    entry.nature,
                    entry.is_shiny,
                    entry.final_id,
                )
        return None, None, None, False, 0

    def detail_payload(self, species_id: int) -> dict | None:
        """Everything the detail page renders, or None when species data is
        unavailable.

        Fetched on demand. The poll loop never calls this -- a learnset is a
        few KB per species and nothing on the main screens needs it.
        """
        metadata = self._species_metadata(species_id)
        if metadata is None:
            return None

        profile, level, nature, is_shiny, identity_id = self._individual_for(species_id)
        # In memory only, deliberately. The derivation is seeded from the
        # individual's own IVs, so recomputing it is free and always gives the
        # same answer -- which means this can be served from a throwaway store
        # on the web thread without a second writer touching the save.
        self._reconcile_profile(profile, identity_id)

        ivs = dict(profile.ivs) if profile is not None else {}
        sprite = ""
        if self.sprites is not None:
            path = self.sprites.path(species_id, animated=False, shiny=is_shiny)
            sprite = str(path) if path else ""

        return {
            "species_id": species_id,
            "name": self.species_name(species_id, self.state.language),
            "sprite_path": sprite,
            "types": metadata.get("types") or [],
            "base_stats": metadata.get("base_stats") or {},
            "moves": metadata.get("moves") or [],
            "version_group": metadata.get("version_group") or "",
            "is_shiny": is_shiny,
            "nature": nature,
            "level": level,
            "gender": profile.gender if profile is not None else None,
            "ability": profile.ability if profile is not None else None,
            "ivs": ivs,
            # Absent IVs mean a record written before profiles existed; the
            # page shows species data and says the individual is unknown,
            # rather than inventing a perfect creature with 0s.
            "has_individual": bool(ivs),
            "stats": (
                profile_mod.computed_stats(
                    ivs, metadata.get("base_stats") or {}, level or 1, nature
                )
                if ivs and level
                else {}
            ),
        }

    def dex_payload(self) -> list[dict]:
        """Species-level collection — ports dexSpecies.

        Folds every species in ``state.dex`` (graduated AND released; the
        Pokedex cares about owned species, not how they were obtained) plus the
        current companion's reached forms.

        ``is_raising`` marks the companion's CURRENT form only. It says "this is
        what you are raising right now", not "this entry might disappear" —
        nothing disappears any more, because a released companion is recorded.
        Putting the badge on every earlier form read as raising several Pokemon
        at once.
        """
        acc: dict[int, dict] = {}

        for entry in self.state.dex:
            for species_id in entry.chain_order:
                slot = acc.setdefault(
                    species_id, {"rarity": str(entry.rarity), "is_shiny": False}
                )
                if entry.is_shiny:
                    slot["is_shiny"] = True

        mon = self.state.active
        for species_id in self._owned_species_of_active():
            slot = acc.setdefault(
                species_id, {"rarity": str(mon.rarity), "is_shiny": False}
            )
            if mon.is_shiny:
                slot["is_shiny"] = True

        current_id = mon.current_id if mon is not None else None
        out = []
        for species_id in sorted(acc):
            slot = acc[species_id]
            sprite = ""
            if self.sprites is not None:
                path = self.sprites.path(
                    species_id, animated=False, shiny=slot["is_shiny"]
                )
                sprite = str(path) if path else ""
            out.append(
                {
                    "final_id": species_id,
                    "species_id": species_id,
                    "name": self.species_name(species_id, self.state.language),
                    "rarity": slot["rarity"],
                    "is_shiny": slot["is_shiny"],
                    "is_raising": species_id == current_id,
                    "sprite_path": sprite,
                }
            )
        return out

    def _chain(self, species_ids, shiny: bool) -> list[dict]:
        out = []
        for species_id in species_ids:
            sprite = ""
            if self.sprites is not None:
                path = self.sprites.path(species_id, animated=False, shiny=shiny)
                sprite = str(path) if path else ""
            out.append(
                {
                    "species_id": species_id,
                    "name": self.species_name(species_id, self.state.language),
                    "sprite_path": sprite,
                }
            )
        return out

    def catch_log_payload(self) -> list[dict]:
        """Every catch, newest first, with its full evolution chain.

        Entries predating caught_at sort last rather than pretending to be
        ancient; ordering among them is unspecified.
        """
        out = [
            {
                "rarity": str(e.rarity),
                "nature": e.nature,
                "is_shiny": e.is_shiny,
                "chain": self._chain(e.chain_order, e.is_shiny),
                "caught_at": e.caught_at,
                "raised_text": _duration(e.raised_seconds),
                "raising": False,
                # The log is the only place graduations and releases are told
                # apart; the Pokedex deliberately treats them the same.
                "released": e.is_released,
            }
            for e in self.state.dex
        ]
        out.sort(key=lambda d: d["caught_at"] or 0, reverse=True)

        # The companion still being raised leads the log, as in the macOS app.
        mon = self.state.active
        if mon is not None:
            out.insert(
                0,
                {
                    "rarity": str(mon.rarity),
                    "nature": mon.nature,
                    "is_shiny": mon.is_shiny,
                    "chain": self._chain(companion.reached_species(mon), mon.is_shiny),
                    "caught_at": mon.hatched_at,
                    "raised_text": "",
                    "raising": True,
                    "released": False,
                },
            )
        return out

    def rarity_counts(self) -> dict:
        """Species counts for the Pokedex filters.

        The catch log counts individuals instead — 14 catches can be 28
        species — so the two tabs cannot share one tally.
        """
        counts = {"legendary": 0, "rare": 0, "uncommon": 0, "common": 0}
        for row in self.dex_payload():
            key = row["rarity"]
            if key in counts:
                counts[key] += 1
        return counts

    def catch_rarity_counts(self) -> dict:
        """Individual counts for the catch log, including the one being raised."""
        counts = {"legendary": 0, "rare": 0, "uncommon": 0, "common": 0}
        for entry in self.state.dex:
            key = str(entry.rarity)
            if key in counts:
                counts[key] += 1
        if self.state.active is not None:
            key = str(self.state.active.rarity)
            if key in counts:
                counts[key] += 1
        return counts

    def _persist(self) -> None:
        if self.read_only:
            return
        save.save(self.state, self.save_path)
