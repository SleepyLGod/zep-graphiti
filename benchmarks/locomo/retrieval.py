"""Fixed two-stage native Graphiti retrieval recipe."""

from dataclasses import asdict, dataclass
from datetime import datetime
from time import perf_counter
from typing import Any

from benchmarks.locomo.config import FACT_BFS_MAX_DEPTH, FACT_LIMIT, NODE_LIMIT
from graphiti_core import Graphiti
from graphiti_core.search.search_config import SearchConfig
from graphiti_core.search.search_config_recipes import (
    EDGE_HYBRID_SEARCH_CROSS_ENCODER,
    NODE_HYBRID_SEARCH_RRF,
)


class RetrievalError(RuntimeError):
    """Raised when the fixed native retrieval contract cannot be executed."""


@dataclass(frozen=True)
class RetrievalResult:
    """Ordered node and fact results from the fixed two-stage search."""

    query: str
    nodes: tuple[dict[str, Any], ...]
    facts: tuple[dict[str, Any], ...]
    node_ids: tuple[str, ...]
    fact_ids: tuple[str, ...]
    node_latency_ms: float
    fact_latency_ms: float

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe artifact payload."""
        return asdict(self)


def build_node_search_config() -> SearchConfig:
    """Clone the native node RRF recipe and apply the benchmark limit."""
    config = NODE_HYBRID_SEARCH_RRF.model_copy(deep=True)
    config.limit = NODE_LIMIT
    return config


def build_fact_search_config() -> SearchConfig:
    """Clone the native fact cross-encoder recipe and fix BFS depth and limit."""
    config = EDGE_HYBRID_SEARCH_CROSS_ENCODER.model_copy(deep=True)
    config.limit = FACT_LIMIT
    if config.edge_config is None:
        raise RetrievalError('Native fact recipe does not contain an edge search config')
    config.edge_config.bfs_max_depth = FACT_BFS_MAX_DEPTH
    return config


async def retrieve(graphiti: Graphiti, query: str) -> RetrievalResult:
    """Run node RRF first, then fact hybrid search from those node origins."""
    node_started = perf_counter()
    node_result = await graphiti.search_(query, config=build_node_search_config())
    node_latency_ms = (perf_counter() - node_started) * 1000
    node_ids = tuple(str(node.uuid) for node in node_result.nodes)
    if not node_ids:
        raise RetrievalError('Node retrieval returned no BFS origins')

    fact_started = perf_counter()
    fact_result = await graphiti.search_(
        query,
        config=build_fact_search_config(),
        bfs_origin_node_uuids=list(node_ids),
    )
    fact_latency_ms = (perf_counter() - fact_started) * 1000
    fact_ids = tuple(str(edge.uuid) for edge in fact_result.edges)
    return RetrievalResult(
        query=query,
        nodes=tuple(
            _node_payload(node, _score_at(node_result.node_reranker_scores, index))
            for index, node in enumerate(node_result.nodes)
        ),
        facts=tuple(
            _fact_payload(edge, _score_at(fact_result.edge_reranker_scores, index))
            for index, edge in enumerate(fact_result.edges)
        ),
        node_ids=node_ids,
        fact_ids=fact_ids,
        node_latency_ms=round(node_latency_ms, 3),
        fact_latency_ms=round(fact_latency_ms, 3),
    )


def _node_payload(node: Any, score: float | None) -> dict[str, Any]:
    return {
        'rank': None,
        'uuid': str(node.uuid),
        'name': getattr(node, 'name', None),
        'summary': getattr(node, 'summary', None),
        'labels': list(getattr(node, 'labels', []) or []),
        'score': score,
    }


def _fact_payload(edge: Any, score: float | None) -> dict[str, Any]:
    return {
        'rank': None,
        'uuid': str(edge.uuid),
        'fact': getattr(edge, 'fact', None),
        'source_node_uuid': getattr(edge, 'source_node_uuid', None),
        'target_node_uuid': getattr(edge, 'target_node_uuid', None),
        'valid_at': _datetime_value(getattr(edge, 'valid_at', None)),
        'invalid_at': _datetime_value(getattr(edge, 'invalid_at', None)),
        'expired_at': _datetime_value(getattr(edge, 'expired_at', None)),
        'episodes': list(getattr(edge, 'episodes', []) or []),
        'score': score,
    }


def _score_at(scores: list[float], index: int) -> float | None:
    return float(scores[index]) if index < len(scores) else None


def _datetime_value(value: object) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None
