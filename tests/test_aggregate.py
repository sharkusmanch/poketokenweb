"""The shared fold: cost provenance, the month axis, and the sum invariant."""

from __future__ import annotations

from datetime import datetime

import pytest

from poketokenbar import aggregate
from poketokenbar.models import Entry


def _entry(day: str, model: str = "claude-sonnet-4-6", tokens: int = 1_000) -> Entry:
    return Entry(
        id=f"{day}|{model}|{tokens}",
        date=datetime.fromisoformat(f"{day}T12:00:00+00:00"),
        local_day=day,
        model=model,
        input=tokens,
    )


# --- cost provenance --------------------------------------------------------


def test_a_priced_day_is_marked_estimated_not_reported():
    daily = aggregate.daily([_entry("2026-09-04")], "2026-09-04")
    assert daily.cost_coverage.estimated is True
    assert daily.cost_coverage.unknown is False
    assert daily.cost_coverage.reported is False


def test_an_unpriced_model_marks_the_day_partial_rather_than_cheap():
    daily = aggregate.daily(
        [_entry("2026-09-04"), _entry("2026-09-04", model="brand-new-model")],
        "2026-09-04",
    )
    # The priced half still counts; the total is a floor, and says so.
    assert daily.cost_coverage.estimated is True
    assert daily.cost_coverage.unknown is True
    assert daily.total_cost > 0


def test_a_day_with_only_unpriced_models_has_no_known_cost():
    daily = aggregate.daily([_entry("2026-09-04", model="brand-new-model")], "2026-09-04")
    assert daily.cost_coverage.has_known is False
    assert daily.cost_coverage.unknown is True
    assert daily.total_cost == 0.0


def test_a_zero_token_turn_never_makes_a_total_partial():
    """A turn that used nothing cannot be hiding money, whatever model it names.
    Marking it unknown puts a '+' on a total that is provably complete."""
    empty = _entry("2026-09-04", model="brand-new-model", tokens=0)
    daily = aggregate.daily([_entry("2026-09-04"), empty], "2026-09-04")
    assert daily.cost_coverage.unknown is False


def test_tokens_are_counted_even_when_the_model_cannot_be_priced():
    daily = aggregate.daily([_entry("2026-09-04", model="brand-new-model")], "2026-09-04")
    assert daily.total_tokens == 1_000


def test_a_day_with_no_entries_is_none():
    assert aggregate.daily([_entry("2026-09-04")], "2026-09-05") is None


# --- the month axis ---------------------------------------------------------


def test_the_axis_runs_from_the_first_to_today_inclusive():
    axis = aggregate.month_axis("2026-09-13")
    assert axis[0] == "2026-09-01"
    assert axis[-1] == "2026-09-13"
    assert len(axis) == 13


def test_a_day_with_no_usage_is_an_explicit_zero():
    """Bar position IS the date. Dropping an empty day slides every later bar
    onto the wrong one."""
    result = aggregate.periods([_entry("2026-09-03")], "2026-09-05")
    series = {row["date"]: row["tokens"] for row in result["month_daily"]}
    assert series == {
        "2026-09-01": 0,
        "2026-09-02": 0,
        "2026-09-03": 1_000,
        "2026-09-04": 0,
        "2026-09-05": 0,
    }


def test_the_series_sums_to_the_month_total():
    entries = [_entry("2026-09-01"), _entry("2026-09-03"), _entry("2026-09-03", tokens=7)]
    result = aggregate.periods(entries, "2026-09-05")
    assert sum(row["tokens"] for row in result["month_daily"]) == result["month"]["tokens"]


def test_last_months_entries_are_excluded_from_both_the_total_and_the_axis():
    """A session that began last month and ran into this one is read in full by
    the mtime filter, so its old entries arrive here. Emitting them would paint
    a partially-filled previous month, because the OTHER files from last month
    were never scanned."""
    result = aggregate.periods([_entry("2026-08-30"), _entry("2026-09-02")], "2026-09-05")
    assert result["month"]["tokens"] == 1_000
    assert all(row["date"].startswith("2026-09") for row in result["month_daily"])


def test_a_future_dated_entry_is_excluded_from_the_month_total_too():
    """It has no slot on the axis, so counting it in the total alone would make
    the chart and the number disagree with nothing to explain it."""
    result = aggregate.periods([_entry("2026-09-02"), _entry("2026-09-28")], "2026-09-05")
    assert result["month"]["tokens"] == 1_000
    assert sum(row["tokens"] for row in result["month_daily"]) == 1_000


def test_the_week_starts_on_monday():
    # 2026-09-13 is a Sunday; its week began Monday 2026-09-07.
    result = aggregate.periods([_entry("2026-09-06"), _entry("2026-09-07")], "2026-09-13")
    assert result["week"]["tokens"] == 1_000


# --- merging across providers -----------------------------------------------


def test_merge_period_sums_money_and_ors_provenance():
    running: dict = {}
    aggregate.merge_period(
        running,
        {"tokens": 10, "cost": 1.0, "cost_coverage": {"estimated": True, "unknown": False}},
    )
    aggregate.merge_period(
        running,
        {"tokens": 5, "cost": 0.5, "cost_coverage": {"estimated": False, "unknown": True}},
    )
    assert running["tokens"] == 15
    assert running["cost"] == pytest.approx(1.5)
    assert running["cost_coverage"]["estimated"] is True
    assert running["cost_coverage"]["unknown"] is True


def test_merge_month_daily_takes_the_union_of_the_axes():
    """Providers build their axis from their own view of 'today'. One whose
    scan straddled midnight must not truncate everyone else's last day."""
    merged = aggregate.merge_month_daily(
        [{"date": "2026-09-01", "tokens": 1, "cost": 0.0}],
        [
            {"date": "2026-09-01", "tokens": 2, "cost": 0.0},
            {"date": "2026-09-02", "tokens": 4, "cost": 0.0},
        ],
    )
    assert [(row["date"], row["tokens"]) for row in merged] == [
        ("2026-09-01", 3),
        ("2026-09-02", 4),
    ]


def test_merge_month_daily_sorts_chronologically():
    merged = aggregate.merge_month_daily(
        [{"date": "2026-09-10", "tokens": 1, "cost": 0.0}],
        [{"date": "2026-09-02", "tokens": 1, "cost": 0.0}],
    )
    assert [row["date"] for row in merged] == ["2026-09-02", "2026-09-10"]
