"""Number formatting — ports Sources/PokeTokenBar/Core/TokenFormatter.swift.

Thresholds and decimal counts are copied verbatim; changing them changes what
the panel shows.
"""


def _trim(value: float, decimals: int) -> str:
    s = f"{value:.{decimals}f}"
    s = s.rstrip("0").rstrip(".")
    return s


def compact(value: int) -> str:
    """987 -> '987', 12345 -> '12.3K', 190612940 -> '190.6M', 1.24e9 -> '1.24B'."""
    v = float(abs(value))
    sign = "-" if value < 0 else ""
    if v < 1_000:
        return str(value)
    if v < 1_000_000:
        return sign + _trim(v / 1_000, 1) + "K"
    if v < 1_000_000_000:
        return sign + _trim(v / 1_000_000, 1) + "M"
    return sign + _trim(v / 1_000_000_000, 2) + "B"


def grouped(value: int) -> str:
    """Thousands separators for popup detail — 190612940 -> '190,612,940'."""
    return f"{value:,}"


def cost(usd: float) -> str:
    return f"${usd:.2f}"


def cost_compact(usd: float) -> str:
    """Panel cost: $9.5 / $311 / $12.0K."""
    if usd < 100:
        return f"${usd:.1f}"
    if usd < 10_000:
        return f"${usd:.0f}"
    return f"${usd / 1_000:.1f}K"


def percent(value: float) -> str:
    return f"{value:.0f}%" if value == round(value) else f"{value:.1f}%"


def with_coverage(
    text: str, estimated: bool, unknown: bool, unavailable: str
) -> str:
    """Annotate a money string with what it does not know.

    ``$1.20``   the amount is exact
    ``≈$1.20``  estimated from the local rate table
    ``≈$1.20+`` estimated AND something could not be priced — a floor, not a total
    ``unavailable`` nothing in the period could be priced at all

    The ``+`` is the point of the whole mechanism. Without it a day containing
    one unpriced model renders a total that looks complete, and the reader has
    no way to tell a cheap day from a day we could not read.
    """
    if unknown and not estimated:
        return unavailable
    return ("≈" if estimated else "") + text + ("+" if unknown else "")
