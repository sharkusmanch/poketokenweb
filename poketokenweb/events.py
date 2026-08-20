"""A bounded, persistent celebration log.

``poketokenbar`` publishes a celebration into its state payload for exactly
one poll and then clears it (see ``companion_store._note_celebration`` and its
caller), so a hatch that happens while nobody has the page open is lost
forever. This module persists each celebration as it appears, newest first,
capped at ``MAX_EVENTS``.

The published payload is ``{"kind", "title", "detail"}`` — there is **no id
field**. Consequently two identical hatches are byte-for-byte identical, and
``published_at`` is the only thing telling them apart. Do **not** deduplicate:
dropping a "duplicate" would silently discard a real second hatch.

Writes are atomic (temp file in the same directory + ``os.replace``) so a
crash or a concurrent reader never observes a half-written log, and reads are
total: a missing, truncated or garbage file yields ``[]`` rather than raising
into the poll loop.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

MAX_EVENTS = 50


def read(path: Path) -> list[dict]:
    """Return stored celebrations, newest first. Never raises."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [entry for entry in data if isinstance(entry, dict)]


def append(
    path: Path,
    celebration: dict | None,
    now: float | None = None,
) -> dict | None:
    """Prepend one celebration to the log; return the stored entry.

    Returns None (and writes nothing) for an empty or kind-less payload.
    Identical payloads are always both recorded — see the module docstring.
    """
    if not celebration:
        return None
    kind = celebration.get("kind")
    if not kind:
        return None

    entry = {
        "kind": kind,
        "title": celebration.get("title") or "",
        "detail": celebration.get("detail") or "",
        "published_at": time.time() if now is None else now,
    }
    events = read(path)
    events.insert(0, entry)
    del events[MAX_EVENTS:]
    _write_atomic(Path(path), events)
    return entry


def _write_atomic(path: Path, events: list[dict[str, Any]]) -> None:
    payload = json.dumps(events, ensure_ascii=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
