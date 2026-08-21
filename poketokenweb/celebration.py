"""Turn an engine celebration into something worth reading on a phone.

The engine emits {kind, title, detail} and nothing else -- "Oshawott came out
of the egg." It knows the rarity, the nature and how long the companion took to
raise, but none of that reaches the notification, which is a shame when the
push is the only moment most of these events are ever witnessed.

Everything here is derived from the SAME poll payload the celebration arrived
in, so no engine change is needed:

* hatched / shiny / evolved read `companion`, which is the mon in question.
* graduated reads `catch_log[0]` instead -- by the time the engine publishes a
  graduation it has already replaced the companion with a fresh egg, so
  `companion` describes the successor, not the graduate.

Text is English-only, matching the engine's own celebration strings, which are
hardcoded rather than run through the l10n catalogue.
"""

from __future__ import annotations

from dataclasses import dataclass

# One per celebration kind. The engine's titles are plain sentences, so the
# emoji is what makes an event recognisable in a notification list at a glance.
EMOJI = {
    "hatched": "🐣",
    "shiny": "✨",
    "evolved": "⚡",
    "graduated": "🎓",
    "ditto": "🫠",
}

# Rarity arrives lowercase from the engine's StrEnum.
RARITY_LABEL = {
    "common": "Common",
    "uncommon": "Uncommon",
    "rare": "Rare",
    "legendary": "Legendary",
}


@dataclass(frozen=True, slots=True)
class Announcement:
    kind: str
    title: str
    body: str
    species_id: int | None = None
    shiny: bool = False


def _facts(*parts: object) -> str:
    """Join the non-empty descriptors with a middot."""
    return " · ".join(str(p) for p in parts if p)


def _rarity(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return RARITY_LABEL.get(value.lower(), value.title())


def _nature(value: object) -> str:
    return f"{value} nature" if isinstance(value, str) and value else ""


def describe(celebration: dict | None, payload: dict | None) -> Announcement | None:
    """Build the push for one celebration, or None if there is nothing to say."""
    if not isinstance(celebration, dict):
        return None
    kind = celebration.get("kind")
    if not kind:
        return None

    title = str(celebration.get("title") or "").strip()
    detail = str(celebration.get("detail") or "").strip()
    emoji = EMOJI.get(kind, "")
    titled = f"{emoji} {title}".strip() if title else emoji or kind

    source = payload if isinstance(payload, dict) else {}
    companion = source.get("companion")
    companion = companion if isinstance(companion, dict) else {}

    if kind == "graduated":
        log = source.get("catch_log")
        entry = log[0] if isinstance(log, list) and log else {}
        entry = entry if isinstance(entry, dict) else {}
        chain = entry.get("chain")
        final = chain[-1] if isinstance(chain, list) and chain else {}
        final = final if isinstance(final, dict) else {}
        name = final.get("name")
        raised = entry.get("raised_text")
        body = _facts(
            detail or (f"{name} joined your Pokédex." if name else None),
            "✨ Shiny" if entry.get("is_shiny") else None,
            _rarity(entry.get("rarity")),
            _nature(entry.get("nature")),
            f"raised in {raised}" if raised else None,
        )
        return Announcement(
            kind=kind,
            title=titled,
            body=body or detail,
            species_id=final.get("species_id") if isinstance(final.get("species_id"), int) else None,
            shiny=bool(entry.get("is_shiny")),
        )

    if kind == "ditto":
        # A disguised Ditto has no line, rarity or nature worth reporting --
        # the joke is that everything you thought you knew was wrong.
        return Announcement(kind=kind, title=titled, body=detail)

    stage = companion.get("stage_index")
    total = companion.get("total_forms")
    progress = (
        f"stage {stage + 1} of {total}"
        if isinstance(stage, int) and isinstance(total, int) and total > 1
        else None
    )
    body = _facts(
        detail,
        _rarity(companion.get("rarity")),
        _nature(companion.get("nature")),
        progress,
    )
    species_id = companion.get("species_id")
    return Announcement(
        kind=kind,
        title=titled,
        body=body or detail,
        species_id=species_id if isinstance(species_id, int) else None,
        shiny=bool(companion.get("is_shiny")),
    )
