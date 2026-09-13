"""Verified against the Swift suite's real fixtures.

Expected values come from LocalUsageReaderTests.swift, so these are not
self-referential: they assert the Python port agrees with the shipped macOS
implementation on the same bytes.
"""

import os
from pathlib import Path

import pytest

from poketokenbar.providers.codex import CodexProvider, parse_rollout

FIXTURES_ENV = "POKETOKENBAR_SWIFT_FIXTURES"


def _fixtures() -> Path | None:
    """Where the upstream Swift suite's fixtures live, if they are here at all.

    These tests are the only thing asserting that this port agrees with the
    shipped macOS implementation on real bytes, so a layout that silently skips
    them is expensive: the suite stays green while the parity claim goes
    unchecked. One hardcoded relative path did exactly that.
    """
    override = os.environ.get(FIXTURES_ENV)
    here = Path(__file__).resolve()
    tail = Path("Tests") / "PokeTokenBarTests" / "Fixtures"
    candidates = [
        *( [Path(override)] if override else [] ),
        # The Swift repo checked out as a sibling of this one.
        here.parents[2] / "PokeTokenBar" / tail,
        # The Swift repo checked out as this repo's parent.
        here.parents[2] / tail,
    ]
    return next((path for path in candidates if path.is_dir()), None)


FIXTURES = _fixtures()
FORK = (FIXTURES / "CodexFork") if FIXTURES else None

pytestmark = pytest.mark.skipif(
    FORK is None or not FORK.is_dir(),
    reason=f"Swift fixtures not found; set {FIXTURES_ENV} to run the parity suite",
)


def _entries(*names):
    out = []
    for name in names:
        out.extend(parse_rollout(FORK / name).entries)
    return out


def test_parent_total_matches_the_swift_expectation():
    entries = _entries("parent.jsonl")
    assert sum(e.total for e in entries) == 312_814
    assert len(entries) == 8


def test_fork_contributes_only_its_new_turns():
    # Swift expects 312_814 + 28_138 + 28_263 == 369_215. A fork replays the
    # parent's turns with fresh timestamps, so counting files naively would
    # report 994_843.
    deduped = CodexProvider.dedup(_entries("parent.jsonl", "child.jsonl", "sibling.jsonl"))
    assert sum(e.total for e in deduped) == 369_215


def test_naive_concatenation_would_overcount():
    # Guards the dedup: without it the total nearly triples.
    raw = _entries("parent.jsonl", "child.jsonl", "sibling.jsonl")
    assert sum(e.total for e in raw) == 994_843


def test_dedup_keeps_the_forks_own_turns():
    deduped = CodexProvider.dedup(_entries("parent.jsonl", "child.jsonl", "sibling.jsonl"))
    totals = {e.total for e in deduped}
    assert 28_138 in totals
    assert 28_263 in totals


def test_input_excludes_cached_tokens():
    # input is reported inclusive of cache; the entry must hold them apart or
    # the same tokens are counted twice.
    entry = parse_rollout(FORK / "parent.jsonl").entries[0]
    assert entry.input == 20_107 - 279 - 2_432
    assert entry.cache_read == 2_432
    assert entry.output == 279


def test_subagent_fixtures_parse_without_error():
    subagent = FIXTURES / "CodexSubagent"
    if not subagent.is_dir():
        pytest.skip("subagent fixtures absent")
    for path in sorted(subagent.glob("*.jsonl")):
        parse_rollout(path)  # must not raise


def test_fork_replay_phantom_contributes_nothing():
    """The real trigger for the #279 rule, on the bytes that produced it.

    child.jsonl's last-but-one event has every `last_token_usage` component at
    0 and `total_tokens` 6742, while the cumulative vector is a full breakdown
    that did NOT move (312_814 before and after). Those 6742 tokens were never
    part of the session's cumulative growth.

    This is the injection check for `_trust_total_only`: relax it to "always
    trust a positive total" and this test fails by exactly 6742, as do the two
    whole-file totals above.
    """
    entries = parse_rollout(FORK / "child.jsonl").entries
    phantom = [e for e in entries if e.id.endswith("|6742")]
    assert len(phantom) == 1, "the zero-context turn must still exist as an entry"
    assert phantom[0].total == 0
