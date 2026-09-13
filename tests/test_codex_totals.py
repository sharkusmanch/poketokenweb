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


# --- dedup identity and ordering (review findings) --------------------------


def _turn(ts: str, cum_in: int, cum_out: int, last_in: int, last_out: int) -> str:
    return _event(
        ts,
        _vector(input_=last_in, output=last_out, total=last_in + last_out),
        _vector(input_=cum_in, output=cum_out, total=cum_in + cum_out),
    )


def test_archiving_a_forked_sessions_parent_keeps_its_usage_on_its_own_day(tmp_path):
    """A fork replays the parent's turns carrying the FORK's timestamps. Keeping
    whichever copy was scanned first made the answer depend on directory order,
    and adding archived_sessions as a second root changed that order."""
    parent = [_meta("p"), _turn("2026-07-13T10:00:00.000Z", 1000, 100, 1000, 100)]
    # The fork replays that turn with its own timestamp, then adds one.
    child = [
        _meta("c"),
        _turn("2026-07-28T09:00:00.000Z", 1000, 100, 1000, 100),
        _turn("2026-07-28T09:05:00.000Z", 1500, 150, 500, 50),
    ]

    _rollout(tmp_path / ".codex" / "sessions", "rollout-parent.jsonl", parent)
    _rollout(tmp_path / ".codex" / "sessions", "rollout-child.jsonl", child)
    live = CodexProvider(home=tmp_path).scan_entries()
    by_day_live = {}
    for entry in live:
        by_day_live[entry.local_day] = by_day_live.get(entry.local_day, 0) + entry.total

    # Now archive the parent; the fork stays live.
    (tmp_path / ".codex" / "archived_sessions").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".codex" / "sessions" / "rollout-parent.jsonl").rename(
        tmp_path / ".codex" / "archived_sessions" / "rollout-parent.jsonl"
    )
    archived = CodexProvider(home=tmp_path).scan_entries()
    by_day_archived = {}
    for entry in archived:
        by_day_archived[entry.local_day] = by_day_archived.get(entry.local_day, 0) + entry.total

    assert by_day_live == by_day_archived, "archiving must not move tokens between days"
    assert sum(by_day_archived.values()) == 1100 + 550


def test_two_sessions_of_the_same_size_but_different_shape_both_count(tmp_path):
    """For the FIRST turn of every session cumulative == last, so keying on two
    totals alone gave `codex|N|N`: any two sessions that opened with the same
    number of tokens erased one another, however differently composed.

    The key now carries all four components of both vectors, so only a turn
    that matches on every one collides.

    Residual and accepted: two sessions whose opening turns are identical in
    every component really are indistinguishable from a fork replaying its
    parent, and that is what this key exists to collapse. Telling them apart
    needs the parent-closure resolution the Swift reader has and this port does
    not; the failure mode chosen here loses one duplicate-looking session
    rather than double-counting every fork, which is both commoner and larger.
    """
    _rollout(
        tmp_path / ".codex" / "sessions",
        "rollout-0.jsonl",
        [_meta("s0"), _turn("2026-09-04T01:00:00.000Z", 900, 100, 900, 100)],
    )
    _rollout(
        tmp_path / ".codex" / "sessions",
        "rollout-1.jsonl",
        [_meta("s1"), _turn("2026-09-05T01:00:00.000Z", 800, 200, 800, 200)],
    )
    entries = CodexProvider(home=tmp_path).scan_entries()
    assert len(entries) == 2
    assert sum(e.total for e in entries) == 2000


def test_turns_without_a_cumulative_do_not_collapse_onto_each_other(tmp_path):
    """With no cumulative there is nothing replay-stable to key on, so every
    such turn keyed to `codex|0|<delta>` and same-sized turns merged."""
    _rollout(
        tmp_path / ".codex" / "sessions",
        "rollout-nocum.jsonl",
        [
            _meta("nc"),
            _event("2026-09-04T01:00:00.000Z", _vector(input_=9_000, output=1_000, total=10_000)),
            _event("2026-09-04T01:01:00.000Z", _vector(input_=10_350, output=1_150, total=11_500)),
            _event("2026-09-04T01:02:00.000Z", _vector(input_=9_000, output=1_000, total=10_000)),
        ],
    )
    entries = CodexProvider(home=tmp_path).scan_entries()
    assert sum(e.total for e in entries) == 31_500


def test_a_stale_cumulative_does_not_credit_an_earlier_turn_twice(tmp_path):
    """prior_cumulative is only refreshed by events that HAVE a cumulative. An
    intervening event without one already contributed its components, and that
    growth is inside the next cumulative -- comparing against the stale value
    credited it again."""
    _rollout(
        tmp_path / ".codex" / "sessions",
        "rollout-stale.jsonl",
        [
            _meta("stale"),
            _turn("2026-09-04T01:00:00.000Z", 1_000, 100, 1_000, 100),
            # No cumulative on this one; its 5,200 counts from components.
            _event("2026-09-04T01:01:00.000Z", _vector(input_=4_700, output=500, total=5_200)),
            # Component-empty orphan. The cumulative grew only because of the
            # turn above, so this must NOT be counted.
            _event(
                "2026-09-04T01:02:00.000Z",
                _vector(total=5_200),
                _vector(input_=5_700, output=600, total=6_300),
            ),
        ],
    )
    entries = CodexProvider(home=tmp_path).scan_entries()
    assert sum(e.total for e in entries) == 6_300


# --- cost provenance of a total-only turn -----------------------------------


def test_a_total_only_turn_is_counted_but_not_priced(tmp_path):
    """The token COUNT is trustworthy; the split is not. Parking the whole
    total in `input` and pricing it as input overstates a realistic Codex turn
    about 2.4x, because a real one is mostly cache read."""
    path = _rollout(
        tmp_path,
        "rollout-unpriced.jsonl",
        [_meta(), _event("2026-09-04T01:29:58.417Z", _vector(total=51_293), _vector(total=51_293))],
    )
    entry = parse_rollout(path).entries[0]
    assert entry.total == 51_293
    assert entry.cost_unknown is True


def test_a_turn_with_a_real_breakdown_is_priced_normally(tmp_path):
    path = _rollout(
        tmp_path,
        "rollout-priced.jsonl",
        [
            _meta(),
            _event(
                "2026-09-04T01:00:00.000Z",
                _vector(input_=20_107, cached=2_432, output=279, total=20_107),
                _vector(input_=20_107, cached=2_432, output=279, total=20_107),
            ),
        ],
    )
    assert parse_rollout(path).entries[0].cost_unknown is False


def test_an_unpriceable_turn_makes_the_days_cost_partial(tmp_path):
    from poketokenbar import aggregate

    path = _rollout(
        tmp_path,
        "rollout-day.jsonl",
        [_meta(), _event("2026-09-04T12:00:00.000Z", _vector(total=51_293), _vector(total=51_293))],
    )
    entries = parse_rollout(path).entries
    daily = aggregate.daily(entries, entries[0].local_day)
    assert daily.total_tokens == 51_293
    assert daily.cost_coverage.unknown is True
    assert daily.total_cost == 0.0


def test_the_flag_survives_the_scan_cache(tmp_path):
    _rollout(
        tmp_path / ".codex" / "sessions",
        "rollout-c.jsonl",
        [_meta(), _event("2026-09-04T01:00:00.000Z", _vector(total=51_293), _vector(total=51_293))],
    )
    cache = ScanCache(tmp_path / "scan.db")
    try:
        CodexProvider(cache=cache, home=tmp_path).scan_entries()
        warm = CodexProvider(cache=cache, home=tmp_path).scan_entries()
        assert warm[0].cost_unknown is True
    finally:
        cache.close()
