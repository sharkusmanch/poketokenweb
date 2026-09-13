"""Claude Code usage — ports the Claude half of LocalUsageReader.swift.

Rule: keep `type == "assistant"` rows, sum the four token fields of
`message.usage`, deduplicate on `(message.id, requestId)` keeping the entry
with the LARGEST total, and bucket by local date from `timestamp`.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator, Mapping
from datetime import date as _date
from datetime import datetime
from pathlib import Path

from .. import aggregate
from ..cache import ScanCache
from ..models import DailyUsage, Entry, ProviderEnrichment

try:  # orjson is ~2x faster on this workload but must not be required
    import orjson

    def _loads(raw: str | bytes):
        return orjson.loads(raw)

except ModuleNotFoundError:  # pragma: no cover - exercised on hosts without orjson
    import json

    def _loads(raw: str | bytes):
        return json.loads(raw)


def _int(value) -> int:
    return value if isinstance(value, int) else 0


def _parse_timestamp(raw: str) -> datetime | None:
    """ISO-8601 with a trailing 'Z' and optional fractional seconds."""
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def parse_line(line: str) -> Entry | None:
    try:
        obj = _loads(line)
    except Exception:
        return None
    if not isinstance(obj, dict) or obj.get("type") != "assistant":
        return None
    msg = obj.get("message")
    if not isinstance(msg, dict):
        return None
    usage = msg.get("usage")
    if not isinstance(usage, dict):
        return None
    date = _parse_timestamp(obj.get("timestamp", ""))
    if date is None:
        return None
    return Entry(
        id=f"{msg.get('id') or ''}|{obj.get('requestId') or ''}",
        date=date,
        local_day=date.astimezone().strftime("%Y-%m-%d"),
        model=msg.get("model") or "unknown",
        # Only top-level fields. usage["iterations"] repeats these numbers.
        input=_int(usage.get("input_tokens")),
        output=_int(usage.get("output_tokens")),
        cache_write=_int(usage.get("cache_creation_input_tokens")),
        cache_read=_int(usage.get("cache_read_input_tokens")),
    )


def dedup_keep_max(entries: list[Entry]) -> list[Entry]:
    """Keep the largest-total entry per id — the completed one."""
    by_id: dict[str, Entry] = {}
    for e in entries:
        existing = by_id.get(e.id)
        if existing is None or e.total > existing.total:
            by_id[e.id] = e
    return list(by_id.values())


def parse_file(path: Path) -> list[Entry]:
    out: list[Entry] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                # Substring prefilter before JSON decode — the cold scan reads
                # hundreds of MB and most lines are not assistant turns.
                if '"usage"' not in line or '"assistant"' not in line:
                    continue
                entry = parse_line(line)
                if entry is not None:
                    out.append(entry)
    except OSError:
        return []
    return dedup_keep_max(out)


def project_roots(
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
    extra: Iterable[Path] | None = None,
) -> list[Path]:
    """Existing Claude project roots, symlink-deduplicated.

    macOS also probes ~/Library/Application Support/Claude for Claude Desktop
    embedded sessions. That path cannot exist on Linux, so it is omitted rather
    than branched on.

    ``extra`` are already-resolved roots supplied by the caller, for sources
    this env-based discovery cannot know about. They are appended as ordinary
    candidates so they get the same is-a-directory and symlink dedup treatment
    -- an extra root that duplicates a discovered one must not double the scan.
    """
    home = home or Path.home()
    env = os.environ if env is None else env

    candidates = [home / ".claude" / "projects", home / ".config" / "claude" / "projects"]
    configured = env.get("CLAUDE_CONFIG_DIR")
    if configured:
        candidates.append(Path(configured) / "projects")
    candidates.extend(extra or ())

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


def jsonl_files(root: Path) -> Iterator[Path]:
    """Every *.jsonl under root, including inside hidden directories."""
    yield from root.rglob("*.jsonl")


class ClaudeProvider:
    """Claude Code local usage."""

    id = "claude_code"
    display_name = "Claude Code"
    reports_cost = True
    # Bump when parse_line changes shape, to invalidate cached blobs.
    PARSER_VERSION = 1

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
        """Every parsed entry across all roots, globally deduplicated."""
        all_entries: list[Entry] = []
        live: set[str] = set()
        for root in project_roots(home=self._home, extra=self._extra_roots):
            for path in jsonl_files(root):
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
                    entries = parse_file(path)
                    if self._cache is not None:
                        self._cache.put(
                            self.id,
                            path,
                            stat.st_mtime,
                            stat.st_size,
                            self.PARSER_VERSION,
                            entries,
                        )
                all_entries.extend(entries)
        if self._cache is not None:
            self._cache.prune(self.id, live)
        # Global dedup — the same turn may appear under overlapping roots.
        return dedup_keep_max(all_entries)

    def fetch_daily(self, today: str | None = None) -> DailyUsage | None:
        """One day's totals. Priced per entry — a day mixes model rates."""
        day = today or _date.today().strftime("%Y-%m-%d")
        return aggregate.daily(self.scan_entries(), day)

    def fetch_periods(self, today: str | None = None) -> dict:
        """Week-to-date, month-to-date, and this month's daily series.

        The week starts Monday, matching the Swift period grouping.
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
        # Blocks/burn-rate remain unported; the *_ok flags stay false so callers
        # keep their previous values rather than zeroing.
        return ProviderEnrichment()
