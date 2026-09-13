"""Folding parsed entries into the totals the UI shows.

Every provider folds the same way, so the fold lives here once rather than
being copied per provider. Upstream consolidated twelve near-identical copies
of this for exactly one reason (#270): a field added later gets filled in some
copies and not others, and the gap is invisible in an environment that does not
use the provider that was missed. This fork had that bug already — ``Codex``
never implemented ``fetch_periods`` at all, so Codex tokens were absent from
every week and month total while today's numbers included them.

Bucketing is by ``Entry.local_day``, a date string fixed at parse time. Building
the month axis from those strings rather than from calendar arithmetic sidesteps
the DST class of defect entirely: there is no date addition to drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as _date
from datetime import timedelta

from . import pricing
from .models import CostCoverage, DailyUsage


@dataclass(slots=True)
class Bucket:
    """Running totals plus the provenance of the money in them."""

    tokens: int = 0
    input: int = 0
    output: int = 0
    cache_write: int = 0
    cache_read: int = 0
    cost: float = 0.0
    coverage: CostCoverage = field(default_factory=CostCoverage)

    def add(self, entry) -> None:
        self.tokens += entry.total
        self.input += entry.input
        self.output += entry.output
        self.cache_write += entry.cache_write
        self.cache_read += entry.cache_read

        amount = pricing.estimated_cost(
            entry.model, entry.input, entry.output, entry.cache_write, entry.cache_read
        )
        if amount is None:
            # A turn that used no tokens cannot be hiding money, whatever model
            # it names. Marking it unknown would put a "+" on a total that is
            # provably complete.
            if entry.total > 0:
                self.coverage.unknown = True
            return
        self.cost += amount
        self.coverage.estimated = True

    def period_payload(self) -> dict:
        return {
            "tokens": self.tokens,
            "cost": self.cost,
            "cost_coverage": self.coverage.payload(),
        }


def daily(entries, day: str) -> DailyUsage | None:
    """One local day's totals, or ``None`` when that day has no entries."""
    matching = [e for e in entries if e.local_day == day]
    if not matching:
        return None
    bucket = Bucket()
    for entry in matching:
        bucket.add(entry)
    return DailyUsage(
        date=day,
        input_tokens=bucket.input,
        output_tokens=bucket.output,
        cache_creation_tokens=bucket.cache_write,
        cache_read_tokens=bucket.cache_read,
        total_tokens=bucket.tokens,
        total_cost=bucket.cost,
        cost_coverage=bucket.coverage,
    )


def month_axis(day: str) -> list[str]:
    """Every local day from the 1st through ``day``, inclusive.

    The axis is the calendar, not the data. Days with no usage must appear as
    explicit zeros, because in the chart a bar's POSITION is its date — dropping
    an empty day slides every later bar onto the wrong one.
    """
    return [f"{day[:7]}-{number:02d}" for number in range(1, int(day[8:10]) + 1)]


def periods(entries, day: str) -> dict:
    """Week-to-date, month-to-date, and this month's day-by-day series.

    One pass over entries the caller has already loaded — no extra read, no
    re-parse.

    The month total and the daily series are filtered *identically*, which makes
    ``sum(month_daily) == month.tokens`` an invariant rather than a coincidence.
    Both exclude days after ``day``: a future-dated entry (clock skew on the
    writing host) has no slot on the axis, so counting it in the total alone
    would make the chart and the number disagree with nothing to explain it.
    """
    anchor = _date.fromisoformat(day)
    week_start = anchor - timedelta(days=anchor.weekday())  # Monday, as in Swift
    prefix = day[:7]

    week = Bucket()
    month = Bucket()
    by_day: dict[str, Bucket] = {key: Bucket() for key in month_axis(day)}

    for entry in entries:
        in_month = entry.local_day[:7] == prefix and entry.local_day <= day
        if in_month:
            month.add(entry)
            bucket = by_day.get(entry.local_day)
            if bucket is not None:
                bucket.add(entry)

        try:
            entry_day = _date.fromisoformat(entry.local_day)
        except ValueError:
            continue
        if week_start <= entry_day <= anchor:
            week.add(entry)

    return {
        "week": week.period_payload(),
        "month": month.period_payload(),
        "month_daily": [
            {"date": key, "tokens": bucket.tokens, "cost": bucket.cost}
            for key, bucket in by_day.items()
        ],
    }


def merge_period(into: dict, addend: dict) -> dict:
    """Accumulate one provider's ``week``/``month`` entry into a running total."""
    into["tokens"] = into.get("tokens", 0) + addend.get("tokens", 0)
    into["cost"] = into.get("cost", 0.0) + addend.get("cost", 0.0)
    target = into.setdefault(
        "cost_coverage", {"reported": False, "estimated": False, "unknown": False}
    )
    source = addend.get("cost_coverage") or {}
    for flag in ("reported", "estimated", "unknown"):
        target[flag] = bool(target.get(flag)) or bool(source.get(flag))
    return into


def merge_month_daily(into: list[dict], addend: list[dict]) -> list[dict]:
    """Sum two providers' daily series onto one date axis.

    The union of the axes, not either one of them: providers build their axis
    from their own view of "today", and a provider whose scan straddled midnight
    must not be able to truncate everyone else's last day.
    """
    totals: dict[str, dict] = {row["date"]: dict(row) for row in into}
    for row in addend:
        existing = totals.get(row["date"])
        if existing is None:
            totals[row["date"]] = dict(row)
            continue
        existing["tokens"] += row.get("tokens", 0)
        existing["cost"] += row.get("cost", 0.0)
    # "yyyy-MM-dd" sorts lexicographically exactly as it sorts chronologically.
    return [totals[key] for key in sorted(totals)]
