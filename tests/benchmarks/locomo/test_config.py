from decimal import Decimal

from benchmarks.locomo.config import (
    DEEPSEEK_MODEL,
    GRAPHITI_COMMIT,
    LOCOMO_COMMIT,
    LOCOMO_SHA256,
    PricingSnapshot,
)


def test_baseline_versions_are_pinned() -> None:
    assert GRAPHITI_COMMIT == '5e2be0faf7038a5b40e700d757b2c337e96b3a05'
    assert LOCOMO_COMMIT == '3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376'
    assert LOCOMO_SHA256 == '79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4'
    assert DEEPSEEK_MODEL == 'deepseek-v4-flash'


def test_pricing_requires_provider_cache_breakdown() -> None:
    pricing = PricingSnapshot.deepseek_2026_07_17()

    assert pricing.estimate_cost_usd(
        cache_hit_input_tokens=1_000_000,
        cache_miss_input_tokens=1_000_000,
        output_tokens=1_000_000,
    ) == Decimal('0.4228')
    assert (
        pricing.estimate_cost_usd(
            cache_hit_input_tokens=None,
            cache_miss_input_tokens=1,
            output_tokens=1,
        )
        is None
    )
