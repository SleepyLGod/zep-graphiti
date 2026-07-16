from datetime import datetime
from types import SimpleNamespace

import pytest

from benchmarks.locomo.config import FACT_BFS_MAX_DEPTH, FACT_LIMIT, NODE_LIMIT
from benchmarks.locomo.retrieval import (
    RetrievalError,
    build_fact_search_config,
    build_node_search_config,
    retrieve,
)
from graphiti_core.search.search_config_recipes import (
    EDGE_HYBRID_SEARCH_CROSS_ENCODER,
    NODE_HYBRID_SEARCH_RRF,
)


def test_search_configs_are_deep_copies_of_native_recipes() -> None:
    original_node = NODE_HYBRID_SEARCH_RRF.model_dump()
    original_fact = EDGE_HYBRID_SEARCH_CROSS_ENCODER.model_dump()

    node_config = build_node_search_config()
    fact_config = build_fact_search_config()

    assert node_config.limit == NODE_LIMIT
    assert fact_config.limit == FACT_LIMIT
    assert fact_config.edge_config is not None
    assert fact_config.edge_config.bfs_max_depth == FACT_BFS_MAX_DEPTH
    assert NODE_HYBRID_SEARCH_RRF.model_dump() == original_node
    assert EDGE_HYBRID_SEARCH_CROSS_ENCODER.model_dump() == original_fact


class _FakeGraphiti:
    def __init__(self, *, nodes: list[object]) -> None:
        self.nodes = nodes
        self.calls: list[dict] = []

    async def search_(self, query: str, **kwargs: object) -> object:
        self.calls.append({'query': query, **kwargs})
        if len(self.calls) == 1:
            return SimpleNamespace(nodes=self.nodes, node_reranker_scores=[0.9], edges=[])
        return SimpleNamespace(
            nodes=[],
            edges=[
                SimpleNamespace(
                    uuid='fact-1',
                    fact='Caroline is researching adoption agencies.',
                    source_node_uuid='node-1',
                    target_node_uuid='node-2',
                    valid_at=datetime(2023, 5, 25),
                    invalid_at=None,
                    expired_at=None,
                    episodes=['episode-1'],
                )
            ],
            edge_reranker_scores=[0.8],
        )


@pytest.mark.asyncio
async def test_retrieval_passes_non_empty_node_origins_to_fact_bfs() -> None:
    graphiti = _FakeGraphiti(
        nodes=[SimpleNamespace(uuid='node-1', name='Caroline', summary='person', labels=[])]
    )

    result = await retrieve(graphiti, 'question')

    assert graphiti.calls[1]['bfs_origin_node_uuids'] == ['node-1']
    assert result.node_ids == ('node-1',)
    assert result.fact_ids == ('fact-1',)


@pytest.mark.asyncio
async def test_retrieval_rejects_empty_bfs_origins() -> None:
    graphiti = _FakeGraphiti(nodes=[])

    with pytest.raises(RetrievalError, match='BFS origins'):
        await retrieve(graphiti, 'question')

    assert len(graphiti.calls) == 1
