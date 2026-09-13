"""Codex usage — ports the Codex half of LocalUsageReader.swift.

Rollout files at ~/.codex/sessions/**/rollout-*.jsonl carry
`payload.type == "token_count"` events. Each event's `info.last_token_usage`
is the delta for that turn, so entries are summed rather than max-reduced.

Archived sessions are read too. Codex moves a finished session from
`sessions/` to `archived_sessions/` in place, and reading only the live
directory made a week's usage disappear the moment a session was archived
(upstream #181).

Turn identity is `(cumulative total, delta total)`. A forked or resumed session
copies its parent's events verbatim, so the same turn appears in several files;
that key collapses the copies while keeping the fork's own turns.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date as _date
from pathlib import Path

from .. import aggregate
from ..cache import ScanCache
from ..models import DailyUsage, Entry, ProviderEnrichment
from .claude import _parse_timestamp, jsonl_files

# 3: total-only turns contribute their total_tokens (#279), entry ids carry the
# full usage vectors rather than two totals, and a rollout with no cumulative
# falls back to a positional id. Each changes what a blob means, so a blob
# cached under an older version must be re-read.
PARSER_VERSION = 3


def _int(value) -> int:
    return value if isinstance(value, int) else 0


def _components(vector: dict) -> int:
    """Billable component sum, mirroring how an Entry is built below."""
    input_total = _int(vector.get("input_tokens"))
    cached = _int(vector.get("cached_input_tokens"))
    return max(0, input_total - cached) + cached + _int(vector.get("output_tokens"))


def _fingerprint(vector: dict) -> str:
    """The whole usage vector, not just its total.

    The entry id is built from these. Two totals alone is far too little
    entropy: the FIRST turn of every session has `cumulative == last`, so its
    id was `codex|N|N` -- two sessions whose opening turn happened to be the
    same size collapsed into one and the other's tokens vanished.

    Stable across a fork's replay, which is what the id has to preserve: a
    replay copies the parent's vectors verbatim.
    """
    return (
        f"{_int(vector.get('input_tokens'))},"
        f"{_int(vector.get('cached_input_tokens'))},"
        f"{_int(vector.get('output_tokens'))},"
        f"{_int(vector.get('total_tokens'))}"
    )


def _trust_total_only(last_total: int, cumulative: dict | None, prior_total: int | None) -> bool:
    """Whether a component-empty `last_token_usage` should still be counted.

    Codex sometimes emits a turn whose every component field is 0 while
    `total_tokens` is set (~2.5% of turns, upstream #278). Neither "always
    trust" nor "always drop" is right:

    * No cumulative vector at all — the total is the only signal there is.
    * Cumulative is itself component-empty with a positive total — same shape,
      same reasoning.
    * `last.total == cumulative.total` — this turn accounts for the whole
      session, so it is the session's real usage.
    * Cumulative grew since the previous event — the tokens are real and the
      breakdown simply did not arrive.

    Everything else is a fork's post-replay "zero-context" turn: the cumulative
    vector has a full breakdown and is UNCHANGED, and the orphan `last.total`
    was never part of that cumulative growth. Counting it inflates the fork.
    """
    if cumulative is None:
        return True
    cumulative_total = _int(cumulative.get("total_tokens"))
    if _components(cumulative) == 0 and cumulative_total > 0:
        return True
    if cumulative_total == last_total:
        return True
    return prior_total is not None and cumulative_total > prior_total


@dataclass(slots=True)
class ParsedRollout:
    entries: list[Entry]
    session_id: str | None


def parse_rollout(path: Path) -> ParsedRollout:
    """Parse one rollout file into per-turn entries."""
    entries: list[Entry] = []
    session_id: str | None = None
    model = "gpt-5.5"
    prior_cumulative: int | None = None
    turn = 0
    name = path.name

    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"session_id"' in line or '"model"' in line:
                    obj = _load(line)
                    if isinstance(obj, dict):
                        found = _find_session_id(obj)
                        if found and session_id is None:
                            session_id = found
                        found_model = _find_model(obj)
                        if found_model:
                            model = found_model
                if "token_count" not in line:
                    continue
                obj = _load(line)
                if not isinstance(obj, dict):
                    continue
                payload = obj.get("payload")
                if not isinstance(payload, dict) or payload.get("type") != "token_count":
                    continue
                info = payload.get("info")
                if not isinstance(info, dict):
                    continue
                last = info.get("last_token_usage")
                if not isinstance(last, dict):
                    continue
                date = _parse_timestamp(obj.get("timestamp", ""))
                if date is None:
                    continue

                cumulative = info.get("total_token_usage")
                if not isinstance(cumulative, dict):
                    cumulative = None
                # Captured BEFORE this event updates it: the growth test asks
                # whether the cumulative moved *because of* this turn.
                prior = prior_cumulative
                if cumulative is None:
                    # Forget the previous value rather than carrying it across
                    # the gap. An intervening event with no cumulative still
                    # contributed its own components, and that growth is
                    # already inside the NEXT cumulative -- comparing against
                    # the stale value credits it a second time.
                    prior_cumulative = None
                else:
                    prior_cumulative = _int(cumulative.get("total_tokens"))

                input_total = _int(last.get("input_tokens"))
                cached = _int(last.get("cached_input_tokens"))
                output = _int(last.get("output_tokens"))
                last_total = _int(last.get("total_tokens"))

                cost_unknown = False
                if (
                    _components(last) == 0
                    and last_total > 0
                    and _trust_total_only(last_total, cumulative, prior)
                ):
                    # No breakdown exists to split, so the whole total is parked
                    # in input to keep the token COUNT right -- but it must not
                    # then be priced as if it really were all input. A real
                    # Codex turn is mostly cache read, so that would overstate
                    # the cost of these turns roughly 2.4x.
                    non_cached, output_value, cache_read = last_total, 0, 0
                    cost_unknown = True
                else:
                    non_cached, output_value, cache_read = (
                        max(0, input_total - cached),
                        output,
                        cached,
                    )

                # Keyed by the two usage VECTORS, not by file position or
                # timestamp. A fork replays the parent's turns with fresh
                # timestamps but identical vectors, so this collapses the
                # copies while keeping the fork's own turns. The delta is part
                # of the key because a fork emits a zero-delta turn repeating
                # the parent's final cumulative.
                #
                # With no cumulative there is nothing replay-stable to key on,
                # and every such turn would otherwise collapse onto
                # `codex|0|<delta>` across the whole scan. Fall back to the
                # file position, as the Swift reader does for the same reason:
                # a fork of such a session double-counts, which is strictly
                # better than unrelated sessions erasing each other.
                if cumulative is None:
                    entry_id = f"codex|{name}|{turn}"
                else:
                    entry_id = f"codex|{_fingerprint(cumulative)}|{_fingerprint(last)}"
                turn += 1

                entries.append(
                    Entry(
                        id=entry_id,
                        date=date,
                        local_day=date.astimezone().strftime("%Y-%m-%d"),
                        model=model,
                        input=non_cached,
                        output=output_value,
                        cache_write=0,
                        cache_read=cache_read,
                        cost_unknown=cost_unknown,
                    )
                )
    except OSError:
        return ParsedRollout([], None)

    return ParsedRollout(entries, session_id)


def _cumulative(info: dict) -> int:
    total = info.get("total_token_usage")
    return _int(total.get("total_tokens")) if isinstance(total, dict) else 0


def _load(line: str):
    try:
        return json.loads(line)
    except ValueError:
        return None


def _find_session_id(obj: dict) -> str | None:
    for key in ("session_id", "sessionId", "id"):
        value = obj.get(key)
        if isinstance(value, str) and value:
            return value
    payload = obj.get("payload")
    if isinstance(payload, dict):
        return _find_session_id(payload)
    return None


def _find_model(obj: dict) -> str | None:
    value = obj.get("model")
    if isinstance(value, str) and value:
        return value
    payload = obj.get("payload")
    if isinstance(payload, dict):
        return _find_model(payload)
    return None


def _keep_earliest(by_id: dict[str, Entry], entry: Entry) -> None:
    """Collapse a replayed turn onto its ORIGINAL, whichever copy is seen first.

    A fork replays its parent's turns carrying the fork's own timestamps, so
    "keep the first one scanned" made the answer depend on directory order.
    Adding archived_sessions as a second root changed that order, and a live
    fork of an archived parent moved the parent's whole history onto the fork's
    day. Keeping the earliest timestamp is order-independent and picks the
    original, which is the one that dates the usage correctly.
    """
    existing = by_id.get(entry.id)
    if existing is None or entry.date < existing.date:
        by_id[entry.id] = entry


def session_roots(
    home: Path | None = None, extra: Iterable[Path] | None = None
) -> list[Path]:
    """Existing Codex rollout roots, symlink-deduplicated.

    `archived_sessions` is a sibling of `sessions`, not a child, so a recursive
    walk of `sessions` never reaches it. Omitting it silently dropped every
    archived session's usage from the totals.
    """
    home = home or Path.home()
    candidates = [
        home / ".codex" / "sessions",
        home / ".codex" / "archived_sessions",
        *(extra or ()),
    ]

    seen: set[Path] = set()
    roots: list[Path] = []
    for path in candidates:
        if not path.is_dir():
            continue
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        roots.append(path)
    return roots


class CodexProvider:
    id = "codex"
    display_name = "Codex"
    reports_cost = True
    PARSER_VERSION = PARSER_VERSION

    def __init__(
        self,
        cache: ScanCache | None = None,
        home: Path | None = None,
        extra_roots: Iterable[Path] | None = None,
    ) -> None:
        self._cache = cache
        self._home = home
        self._extra_roots = list(extra_roots or ())

    def scan_entries(self) -> list[Entry]:
        """Every parsed entry across all roots, globally deduplicated.

        Cached per file on (mtime, size, parser version), as the Claude provider
        is. Without this a rollout is re-read on every poll, and archived
        sessions made that permanently more expensive: an archived file never
        changes again, so re-parsing it is pure waste for the life of the pod.
        """
        by_id: dict[str, Entry] = {}
        live: set[str] = set()
        for root in session_roots(self._home, extra=self._extra_roots):
            for path in sorted(jsonl_files(root)):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                live.add(str(path))
                entries = None
                if self._cache is not None:
                    entries = self._cache.get(
                        self.id, path, stat.st_mtime, stat.st_size, self.PARSER_VERSION
                    )
                if entries is None:
                    entries = parse_rollout(path).entries
                    if self._cache is not None:
                        self._cache.put(
                            self.id,
                            path,
                            stat.st_mtime,
                            stat.st_size,
                            self.PARSER_VERSION,
                            entries,
                        )
                for entry in entries:
                    _keep_earliest(by_id, entry)
        if self._cache is not None:
            self._cache.prune(self.id, live)
        return list(by_id.values())

    @staticmethod
    def dedup(entries: list[Entry]) -> list[Entry]:
        by_id: dict[str, Entry] = {}
        for e in entries:
            _keep_earliest(by_id, e)
        return list(by_id.values())

    def fetch_daily(self, today: str | None = None) -> DailyUsage | None:
        day = today or _date.today().strftime("%Y-%m-%d")
        return aggregate.daily(self.scan_entries(), day)

    def fetch_periods(self, today: str | None = None) -> dict:
        """Week-to-date, month-to-date, and this month's daily series.

        This provider had no ``fetch_periods`` at all, and the daemon skips a
        provider that lacks it — so Codex tokens appeared in today's total but
        were absent from every week and month figure.
        """
        day = today or _date.today().strftime("%Y-%m-%d")
        return aggregate.periods(self.scan_entries(), day)

    def fetch_snapshot(self, today: str | None = None) -> tuple[DailyUsage | None, dict]:
        """Today's totals and the period totals from ONE scan.

        Separate fetch_daily/fetch_periods calls scanned the logs twice per
        poll, and worse, read them at two different instants -- a file appended
        in between made today's number disagree with its own bar in the monthly
        chart.
        """
        day = today or _date.today().strftime("%Y-%m-%d")
        entries = self.scan_entries()
        return aggregate.daily(entries, day), aggregate.periods(entries, day)

    def fetch_enrichment(self) -> ProviderEnrichment:
        return ProviderEnrichment()
