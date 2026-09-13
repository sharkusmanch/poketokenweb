"""Usage data models — ports Sources/PokeTokenBar/Core/Models.swift."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class Entry:
    """One assistant turn's token usage, as parsed from a provider log line."""

    id: str
    date: datetime
    local_day: str
    model: str
    input: int = 0
    output: int = 0
    cache_write: int = 0
    cache_read: int = 0
    # The token COUNT is trustworthy but the split across input/output/cache is
    # not, so no rate card can price it. Set by the Codex total-only path,
    # where the whole amount is parked in `input` for want of a breakdown --
    # pricing that as 100% input overstates a realistic turn ~2.4x.
    cost_unknown: bool = False

    @property
    def total(self) -> int:
        return self.input + self.output + self.cache_write + self.cache_read


@dataclass(slots=True)
class CostCoverage:
    """Where a cost total came from — ports CostCoverage.swift.

    Provenance, not accuracy. A total can be complete and still be an estimate,
    and it can carry real money and still be missing some.

    ``unknown`` is the field that earns this type its existence: without it, a
    day containing one unpriced model renders ``$0.00`` for that model's share
    and the sum looks authoritative. ``$0.00`` reads as "this was free";
    "unknown" reads as "we could not price it". They are different claims.
    """

    # The source told us the amount. No provider in this fork does; kept so the
    # vocabulary matches upstream if one ever arrives.
    reported: bool = False
    # Computed from the local rate table.
    estimated: bool = False
    # At least one priced-in-principle turn had no rate available.
    unknown: bool = False

    @property
    def has_known(self) -> bool:
        return self.reported or self.estimated

    def merge(self, other: "CostCoverage") -> None:
        self.reported = self.reported or other.reported
        self.estimated = self.estimated or other.estimated
        self.unknown = self.unknown or other.unknown

    def payload(self) -> dict:
        return {
            "reported": self.reported,
            "estimated": self.estimated,
            "unknown": self.unknown,
        }


@dataclass(slots=True)
class DailyUsage:
    date: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    total_tokens: int = 0
    total_cost: float = 0.0
    cost_coverage: CostCoverage = field(default_factory=CostCoverage)


@dataclass(slots=True)
class BlockUsage:
    id: str
    start_time: str
    end_time: str
    is_active: bool = False
    total_tokens: int = 0
    cost_usd: float = 0.0
    tokens_per_minute: float | None = None


@dataclass(slots=True)
class PeriodUsage:
    period: str
    total_tokens: int = 0
    total_cost: float = 0.0
    cost_coverage: CostCoverage = field(default_factory=CostCoverage)


@dataclass(slots=True)
class ProviderEnrichment:
    """Best-effort detail. The *_ok flags distinguish 'failed' from 'empty' —
    on failure the caller keeps its previous values instead of zeroing them.
    """

    active_block: BlockUsage | None = None
    blocks_ok: bool = False
    week_total: PeriodUsage | None = None
    month_total: PeriodUsage | None = None
    periods_ok: bool = False
