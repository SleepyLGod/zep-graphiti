"""Pinned configuration for the native Graphiti LOCOMO baseline."""

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any, Final

GRAPHITI_COMMIT: Final = '5e2be0faf7038a5b40e700d757b2c337e96b3a05'
LOCOMO_COMMIT: Final = '3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376'
LOCOMO_SHA256: Final = '79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4'
LOCOMO_URL: Final = (
    f'https://raw.githubusercontent.com/snap-research/locomo/{LOCOMO_COMMIT}/data/locomo10.json'
)

DEEPSEEK_BASE_URL: Final = 'https://api.deepseek.com'
DEEPSEEK_MODEL: Final = 'deepseek-v4-flash'
DEEPSEEK_MAX_TOKENS: Final = 8192
DEEPSEEK_TEMPERATURE: Final = 0

BGE_EMBEDDING_MODEL: Final = 'BAAI/bge-m3'
BGE_EMBEDDING_DIM: Final = 1024
BGE_RERANKER_MODEL: Final = 'BAAI/bge-reranker-v2-m3'

NEO4J_IMAGE: Final = 'neo4j:5.26.2'
DEFAULT_SAMPLE_INDEX: Final = 0
DEFAULT_START_ROW: Final = 26
DEFAULT_ROW_LIMIT: Final = 3
DEFAULT_QUERY: Final = 'What is Caroline researching, and why?'
NODE_LIMIT: Final = 20
FACT_LIMIT: Final = 20
FACT_BFS_MAX_DEPTH: Final = 3


@dataclass(frozen=True)
class PricingSnapshot:
    """Immutable provider pricing used only for reproducible cost estimates."""

    effective_date: str
    source_url: str
    cache_hit_input_per_million_usd: Decimal
    cache_miss_input_per_million_usd: Decimal
    output_per_million_usd: Decimal

    @classmethod
    def deepseek_2026_07_17(cls) -> 'PricingSnapshot':
        """Return the pricing snapshot fixed by the benchmark contract."""
        return cls(
            effective_date='2026-07-17',
            source_url='https://api-docs.deepseek.com/quick_start/pricing',
            cache_hit_input_per_million_usd=Decimal('0.0028'),
            cache_miss_input_per_million_usd=Decimal('0.14'),
            output_per_million_usd=Decimal('0.28'),
        )

    def estimate_cost_usd(
        self,
        *,
        cache_hit_input_tokens: int | None,
        cache_miss_input_tokens: int | None,
        output_tokens: int | None,
    ) -> Decimal | None:
        """Estimate cost only when the provider returned every required counter."""
        if (
            cache_hit_input_tokens is None
            or cache_miss_input_tokens is None
            or output_tokens is None
        ):
            return None
        million = Decimal(1_000_000)
        return (
            Decimal(cache_hit_input_tokens) * self.cache_hit_input_per_million_usd
            + Decimal(cache_miss_input_tokens) * self.cache_miss_input_per_million_usd
            + Decimal(output_tokens) * self.output_per_million_usd
        ) / million

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe pricing manifest."""
        return {
            key: str(value) if isinstance(value, Decimal) else value
            for key, value in asdict(self).items()
        }
