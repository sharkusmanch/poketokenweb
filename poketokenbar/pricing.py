"""Per-model token rates — ports ModelPricing.swift.

Rates are USD per million tokens. They estimate API-equivalent spend; they are
not an invoice, and a subscription bill will not match them.

Two questions are answered separately here, and conflating them was the defect
upstream fixed in #289:

  * :func:`rate` — "what does a token of this model cost?" Returns ``ZERO`` for
    anything unrecognised, which is the historical contract every existing
    caller relies on.
  * :func:`estimated_cost` — "can this model be priced at all?" Returns ``None``
    when nothing prices it, so a caller can tell an unpriced model apart from a
    model that genuinely costs nothing. ``$0.00`` and "we don't know" must never
    render the same; the first reads as free, the second as missing.

Family fallbacks are deliberately KEPT. Upstream deleted them in #289 and now
reports ``claude-opus-5`` as cost-unavailable because it has no exact row. Opus
5 is the dominant model in this deployment's logs, so losing the fallback would
turn almost every day's cost into "unavailable" — strictly worse than an
estimate that is right to the cent for every Opus-family release so far.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelRate:
    input: float = 0.0
    output: float = 0.0
    cache_write: float = 0.0
    cache_read: float = 0.0


def per_million(
    input_: float, output: float, cache_write: float, cache_read: float
) -> ModelRate:
    m = 1_000_000
    return ModelRate(input_ / m, output / m, cache_write / m, cache_read / m)


ZERO = ModelRate()

TABLE: dict[str, ModelRate] = {
    "claude-opus-4-8": per_million(5, 25, 6.25, 0.5),
    "claude-opus-4-7": per_million(5, 25, 6.25, 0.5),
    "claude-sonnet-4-6": per_million(3, 15, 3.75, 0.3),
    "claude-haiku-4-5-20251001": per_million(1, 5, 1.25, 0.1),
    # Fable 5 was unpriced in the ccusage/LiteLLM snapshot the table was first
    # derived from, so it sat at $0 and silently undercounted every token.
    "claude-fable-5": per_million(10, 50, 12.5, 1.0),
    # Fable 5.1 shares Fable 5's input/output/cache-write rates and differs ONLY
    # in cache read ($1.00 -> $0.25). That single field is why the `fable`
    # family fallback is not enough: it would apply Fable 5's cache-read rate
    # and overstate a cache-heavy session's cost four times over.
    "claude-fable-5-1": per_million(10, 50, 12.5, 0.25),
    "gpt-5.5": per_million(5, 30, 0, 0.5),
    # Gemini official API rates (base tier, <=200K prompt). Cache is the read
    # rate only; storage-time charges are not modelled.
    "gemini-2.5-pro": per_million(1.25, 10, 0, 0.3125),
    "gemini-2.5-flash": per_million(0.30, 2.5, 0, 0.075),
    "gemini-2.0-flash": per_million(0.10, 0.4, 0, 0.025),
}


def _resolve(model: str) -> ModelRate | None:
    """The rate for a model, or ``None`` when nothing prices it.

    Exact match first, then a family fallback for version drift. The order of
    the two zero-rate guards matters and is not cosmetic — see the comments.
    """
    exact = TABLE.get(model)
    if exact is not None:
        return exact

    m = (model or "").lower()

    # Grok reports its own cost; there is no rate card. This must precede the
    # family fallbacks so names like grok-codex-* are not priced as GPT. ZERO
    # rather than None: "this model is not billed per token" is knowledge, not
    # an absence of it.
    if m.startswith("grok"):
        return ZERO
    # Antigravity is subscription-billed and reports no amount. Its
    # "antigravity/" prefix also dodges the exact table, which matters because
    # that CLI calls models like claude-sonnet-4-6 that would otherwise match.
    if m.startswith("antigravity/"):
        return ZERO

    if "fable" in m:
        # Fable 5's rates, not 5.1's. A future fable release is more likely to
        # keep the long-standing $1.00 cache read than the 5.1 discount, and
        # over-reporting a cost is the safer direction to be wrong in.
        return per_million(10, 50, 12.5, 1.0)
    if "opus" in m:
        return per_million(5, 25, 6.25, 0.5)
    if "sonnet" in m:
        return per_million(3, 15, 3.75, 0.3)
    if "haiku" in m:
        return per_million(1, 5, 1.25, 0.1)
    if "gpt" in m or "codex" in m or "o4" in m or "o3" in m:
        return per_million(5, 30, 0, 0.5)
    if m.startswith("gemini"):
        if "pro" in m:
            return per_million(1.25, 10, 0, 0.3125)
        if "flash" in m:
            return per_million(0.30, 2.5, 0, 0.075)
    return None


def rate(model: str) -> ModelRate:
    """Exact match first, then a family fallback. ``ZERO`` when unrecognised."""
    resolved = _resolve(model)
    return ZERO if resolved is None else resolved


def is_priced(model: str) -> bool:
    """Whether any rate applies to this model name."""
    return _resolve(model) is not None


def estimated_cost(
    model: str, input_: int, output: int, cache_write: int, cache_read: int
) -> float | None:
    """Cost in USD, or ``None`` when the model has no rate at all.

    ``None`` is not zero. A caller that adds it as zero reports a total that
    looks complete while silently omitting an unknown amount.
    """
    r = _resolve(model)
    if r is None:
        return None
    return (
        input_ * r.input
        + output * r.output
        + cache_write * r.cache_write
        + cache_read * r.cache_read
    )


def cost(model: str, input_: int, output: int, cache_write: int, cache_read: int) -> float:
    """Historical float-returning form. An unpriced model costs 0."""
    amount = estimated_cost(model, input_, output, cache_write, cache_read)
    return 0.0 if amount is None else amount
