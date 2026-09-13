"""Codex parser rules that have no Swift fixture: #279, #181, #187.

These are synthetic because the shapes they cover either do not appear in the
shipped fixtures (total-only turns mid-session) or are a directory layout
rather than a file (archived sessions, extra roots).
"""

from __future__ import annotations

import json

import pytest

from poketokenbar.cache import ScanCache
from poketokenbar.providers.codex import CodexProvider, parse_rollout, session_roots


def _vector(input_=0, cached=0, output=0, total=0) -> dict:
    return {
        "input_tokens": input_,
        "cached_input_tokens": cached,
        "output_tokens": output,
        "reasoning_output_tokens": 0,
        "total_tokens": total,
    }


def _event(ts: str, last: dict, cumulative: dict | None = None) -> str:
    info: dict = {}
    if cumulative is not None:
        info["total_token_usage"] = cumulative
    info["last_token_usage"] = last
    return json.dumps(
        {
            "timestamp": ts,
            "type": "event_msg",
            "payload": {"type": "token_count", "info": info},
        }
    )


def _meta(session_id: str = "s1") -> str:
    return json.dumps(
        {
            "timestamp": "2026-09-04T01:00:00.000Z",
            "type": "session_meta",
            "payload": {"id": session_id, "model": "gpt-5.5"},
        }
    )


def _rollout(directory, name: str, lines: list[str]):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# --- #279: component-empty turns that still carry a total -------------------


def test_total_only_turn_counts_when_it_matches_the_session_total(tmp_path):
    path = _rollout(
        tmp_path,
        "rollout-solo.jsonl",
        [
            _meta(),
            _event(
                "2026-09-04T01:29:58.417Z",
                _vector(total=51_293),
                _vector(total=51_293),
            ),
        ],
    )
    entries = parse_rollout(path).entries
    assert [e.total for e in entries] == [51_293]
    # No breakdown exists, so the whole amount lands in input rather than
    # being split across buckets the log never reported.
    assert entries[0].input == 51_293
    assert entries[0].output == 0
    assert entries[0].cache_read == 0


def test_total_only_turn_counts_when_cumulative_is_absent(tmp_path):
    path = _rollout(
        tmp_path,
        "rollout-nocum.jsonl",
        [_meta(), _event("2026-09-04T01:29:58.417Z", _vector(total=51_293))],
    )
    assert [e.total for e in parse_rollout(path).entries] == [51_293]


def test_total_only_turn_counts_when_cumulative_grew(tmp_path):
    path = _rollout(
        tmp_path,
        "rollout-grew.jsonl",
        [
            _meta(),
            _event(
                "2026-09-04T01:00:00.000Z",
                _vector(input_=1000, output=100, total=1100),
                _vector(input_=1000, output=100, total=1100),
            ),
            # Components empty, but the session's cumulative moved by 5000.
            _event(
                "2026-09-04T01:01:00.000Z",
                _vector(total=5000),
                _vector(input_=1000, output=100, total=6100),
            ),
        ],
    )
    assert [e.total for e in parse_rollout(path).entries] == [1100, 5000]


def test_total_only_turn_is_dropped_when_cumulative_did_not_move(tmp_path):
    """The fork-replay shape: a full cumulative breakdown that stayed put.

    This is the branch that makes #279 safe. Without it the orphan total is
    added to a session that did not grow, and every fork double-counts.
    """
    path = _rollout(
        tmp_path,
        "rollout-phantom.jsonl",
        [
            _meta(),
            _event(
                "2026-09-04T01:00:00.000Z",
                _vector(input_=1000, output=100, total=1100),
                _vector(input_=1000, output=100, total=1100),
            ),
            _event(
                "2026-09-04T01:01:00.000Z",
                _vector(total=6742),
                _vector(input_=1000, output=100, total=1100),
            ),
        ],
    )
    entries = parse_rollout(path).entries
    assert [e.total for e in entries] == [1100, 0]


def test_a_turn_with_a_real_breakdown_is_unaffected(tmp_path):
    path = _rollout(
        tmp_path,
        "rollout-normal.jsonl",
        [
            _meta(),
            _event(
                "2026-09-04T01:00:00.000Z",
                _vector(input_=20_107, cached=2_432, output=279, total=20_107),
                _vector(input_=20_107, cached=2_432, output=279, total=20_107),
            ),
        ],
    )
    entry = parse_rollout(path).entries[0]
    assert entry.input == 20_107 - 2_432
    assert entry.cache_read == 2_432
    assert entry.output == 279


# --- #181: archived sessions ------------------------------------------------


def test_archived_sessions_are_a_scan_root(tmp_path):
    (tmp_path / ".codex" / "sessions").mkdir(parents=True)
    (tmp_path / ".codex" / "archived_sessions").mkdir(parents=True)
    roots = session_roots(home=tmp_path)
    assert [r.name for r in roots] == ["sessions", "archived_sessions"]


def test_archiving_a_session_does_not_lose_its_usage(tmp_path):
    """Codex moves a finished session sideways; the tokens must survive it."""
    archived = tmp_path / ".codex" / "archived_sessions"
    _rollout(
        archived,
        "rollout-old.jsonl",
        [
            _meta("archived"),
            _event(
                "2026-09-04T01:00:00.000Z",
                _vector(input_=900, output=100, total=1000),
                _vector(input_=900, output=100, total=1000),
            ),
        ],
    )
    (tmp_path / ".codex" / "sessions").mkdir(parents=True)

    entries = CodexProvider(home=tmp_path).scan_entries()
    assert sum(e.total for e in entries) == 1000


def test_missing_archive_directory_is_not_an_error(tmp_path):
    (tmp_path / ".codex" / "sessions").mkdir(parents=True)
    assert [r.name for r in session_roots(home=tmp_path)] == ["sessions"]


def test_the_same_turn_in_both_roots_is_counted_once(tmp_path):
    """An archive that was copied rather than moved must not double the total."""
    lines = [
        _meta("dup"),
        _event(
            "2026-09-04T01:00:00.000Z",
            _vector(input_=900, output=100, total=1000),
            _vector(input_=900, output=100, total=1000),
        ),
    ]
    _rollout(tmp_path / ".codex" / "sessions", "rollout-x.jsonl", lines)
    _rollout(tmp_path / ".codex" / "archived_sessions", "rollout-x.jsonl", lines)

    entries = CodexProvider(home=tmp_path).scan_entries()
    assert sum(e.total for e in entries) == 1000


# --- #187: extra roots ------------------------------------------------------


def test_extra_roots_are_scanned(tmp_path):
    (tmp_path / ".codex" / "sessions").mkdir(parents=True)
    elsewhere = tmp_path / "mounted" / "codex"
    _rollout(
        elsewhere,
        "rollout-extra.jsonl",
        [
            _meta("extra"),
            _event(
                "2026-09-04T01:00:00.000Z",
                _vector(input_=400, output=100, total=500),
                _vector(input_=400, output=100, total=500),
            ),
        ],
    )

    provider = CodexProvider(home=tmp_path, extra_roots=[elsewhere])
    assert sum(e.total for e in provider.scan_entries()) == 500


def test_an_extra_root_duplicating_a_default_does_not_double_the_scan(tmp_path):
    sessions = tmp_path / ".codex" / "sessions"
    _rollout(
        sessions,
        "rollout-y.jsonl",
        [
            _meta("same"),
            _event(
                "2026-09-04T01:00:00.000Z",
                _vector(input_=900, output=100, total=1000),
                _vector(input_=900, output=100, total=1000),
            ),
        ],
    )
    provider = CodexProvider(home=tmp_path, extra_roots=[sessions])
    assert sum(e.total for e in provider.scan_entries()) == 1000


# --- caching ----------------------------------------------------------------


def test_the_scan_cache_round_trips_entries(tmp_path):
    """The provider accepted a cache and never used it, so every poll re-read
    every rollout. An archived file never changes again, which made that waste
    permanent."""
    _rollout(
        tmp_path / ".codex" / "sessions",
        "rollout-z.jsonl",
        [
            _meta("cached"),
            _event(
                "2026-09-04T01:00:00.000Z",
                _vector(input_=900, output=100, total=1000),
                _vector(input_=900, output=100, total=1000),
            ),
        ],
    )
    cache = ScanCache(tmp_path / "scan.db")
    try:
        first = CodexProvider(cache=cache, home=tmp_path).scan_entries()
        second = CodexProvider(cache=cache, home=tmp_path).scan_entries()
        assert sum(e.total for e in first) == 1000
        assert sum(e.total for e in second) == 1000
        assert {e.id for e in first} == {e.id for e in second}
    finally:
        cache.close()


def test_parser_version_is_ahead_of_the_total_only_change():
    """A blob cached under v1 dropped total-only turns; it must be re-parsed."""
    assert CodexProvider.PARSER_VERSION >= 2


# --- periods ----------------------------------------------------------------


def test_codex_now_reports_periods(tmp_path):
    """Codex had no fetch_periods, and the daemon skips providers without one,
    so Codex tokens were in today's number but in no week or month total."""
    _rollout(
        tmp_path / ".codex" / "sessions",
        "rollout-p.jsonl",
        [
            _meta("periods"),
            _event(
                "2026-09-04T01:00:00.000Z",
                _vector(input_=900, output=100, total=1000),
                _vector(input_=900, output=100, total=1000),
            ),
        ],
    )
    provider = CodexProvider(home=tmp_path)
    assert hasattr(provider, "fetch_periods")
    result = provider.fetch_periods(today="2026-09-30")
    assert result["month"]["tokens"] == pytest.approx(1000)
    assert sum(row["tokens"] for row in result["month_daily"]) == 1000
