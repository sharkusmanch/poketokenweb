import pytest

from poketokenbar import pricing


def test_exact_table_match():
    r = pricing.rate("claude-sonnet-4-6")
    assert r.input == pytest.approx(3 / 1_000_000)
    assert r.output == pytest.approx(15 / 1_000_000)


def test_family_fallback_for_unknown_version():
    # Version drift must not silently zero a real model's cost.
    assert pricing.rate("claude-opus-9-9").input == pytest.approx(5 / 1_000_000)


def test_grok_is_zero_before_any_family_fallback():
    # grok-codex-* would otherwise match the "codex" -> GPT fallback and show a
    # fabricated dollar amount.
    assert pricing.rate("grok-codex-fast") == pricing.ZERO
    assert pricing.rate("grok-4o-mini") == pricing.ZERO


def test_antigravity_prefix_is_zero_even_for_a_priced_model():
    # That CLI calls claude-sonnet-4-6, which would match the exact table
    # without the prefix. It is subscription-billed, so cost must stay 0.
    assert pricing.rate("antigravity/claude-sonnet-4-6") == pricing.ZERO


def test_unknown_model_costs_nothing_rather_than_guessing():
    assert pricing.rate("totally-unknown-model") == pricing.ZERO


def test_gemini_family_fallback():
    assert pricing.rate("gemini-3.0-pro").input == pytest.approx(1.25 / 1_000_000)
    assert pricing.rate("gemini-3.0-flash").input == pytest.approx(0.30 / 1_000_000)


def test_unknown_gemini_variant_is_zero():
    assert pricing.rate("gemini-experimental-x") == pricing.ZERO


def test_cost_sums_all_four_token_kinds():
    # 1M of each against sonnet: 3 + 15 + 3.75 + 0.3
    total = pricing.cost("claude-sonnet-4-6", 1_000_000, 1_000_000, 1_000_000, 1_000_000)
    assert total == pytest.approx(22.05)


def test_fable_5_is_priced_rather_than_free():
    # It sat at $0 because it was unpriced in the snapshot the table came from,
    # which silently undercounted every Fable token.
    total = pricing.cost("claude-fable-5", 1_000_000, 1_000_000, 1_000_000, 1_000_000)
    assert total == pytest.approx(10 + 50 + 12.5 + 1.0)


def test_fable_5_1_cache_reads_are_a_quarter_of_fable_5s():
    """The one field that differs between the two, and the reason an exact row
    is required: the `fable` family fallback applies Fable 5's $1.00 and
    overstates a cache-heavy session four times over."""
    assert pricing.rate("claude-fable-5-1").cache_read == pytest.approx(0.25 / 1_000_000)
    assert pricing.cost("claude-fable-5-1", 0, 0, 0, 1_000_000) == pytest.approx(0.25)
    # Everything else matches Fable 5.
    for field in ("input", "output", "cache_write"):
        assert getattr(pricing.rate("claude-fable-5-1"), field) == pytest.approx(
            getattr(pricing.rate("claude-fable-5"), field)
        )


def test_a_future_fable_falls_back_to_the_safer_cache_rate():
    assert pricing.rate("claude-fable-6").cache_read == pytest.approx(1.0 / 1_000_000)


def test_opus_5_keeps_its_family_fallback():
    """Upstream deleted the family fallbacks and now reports opus-5 as
    unavailable. It is the dominant model in this deployment's logs, so the
    fallback is kept deliberately."""
    assert pricing.rate("claude-opus-5").input == pytest.approx(5 / 1_000_000)
    assert pricing.is_priced("claude-opus-5")


# --- priced vs unpriced -----------------------------------------------------


def test_estimated_cost_is_none_only_when_nothing_prices_the_model():
    assert pricing.estimated_cost("totally-unknown", 1_000, 0, 0, 0) is None
    assert pricing.estimated_cost("claude-sonnet-4-6", 1_000, 0, 0, 0) is not None


def test_a_deliberately_free_model_is_priced_at_zero_not_unknown():
    """Grok and Antigravity report no per-token charge. That is knowledge, not
    an absence of it, so they must not raise the 'unknown' flag."""
    assert pricing.estimated_cost("grok-codex-fast", 10**6, 0, 0, 0) == 0.0
    assert pricing.estimated_cost("antigravity/claude-sonnet-4-6", 10**6, 0, 0, 0) == 0.0
    assert pricing.is_priced("grok-codex-fast")


def test_cost_keeps_its_float_contract_for_existing_callers():
    assert pricing.cost("totally-unknown", 10**6, 0, 0, 0) == 0.0


def test_the_claude_parsers_unknown_placeholder_is_genuinely_unpriced():
    """providers.claude substitutes the literal "unknown" when a log line has
    no model field, so this is a reachable input, not a synthetic one."""
    assert pricing.estimated_cost("unknown", 1_000, 0, 0, 0) is None
