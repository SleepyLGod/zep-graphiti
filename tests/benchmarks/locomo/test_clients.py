import numpy as np
import pytest

from benchmarks.locomo.clients import BgeM3Embedder, CpuBgeReranker


class _FakeEmbeddingModel:
    def __init__(self) -> None:
        self.calls: list[tuple[object, dict]] = []

    def encode(self, inputs: object, **kwargs: object) -> np.ndarray:
        self.calls.append((inputs, kwargs))
        count = len(inputs) if isinstance(inputs, list) else 1
        return np.ones((count, 3))


class _FakeRerankerModel:
    def predict(self, pairs: list[list[str]]) -> np.ndarray:
        assert pairs == [['query', 'first'], ['query', 'second']]
        return np.array([0.1, 0.9])


@pytest.mark.asyncio
async def test_bge_embedder_normalizes_single_and_batch_embeddings() -> None:
    model = _FakeEmbeddingModel()
    embedder = BgeM3Embedder(model=model, embedding_dim=3)

    assert await embedder.create('hello') == [1.0, 1.0, 1.0]
    assert await embedder.create_batch(['a', 'b']) == [[1.0, 1.0, 1.0], [1.0, 1.0, 1.0]]
    assert all(call[1]['normalize_embeddings'] is True for call in model.calls)


@pytest.mark.asyncio
async def test_cpu_reranker_preserves_graphiti_rank_contract() -> None:
    reranker = CpuBgeReranker(model=_FakeRerankerModel())

    assert await reranker.rank('query', ['first', 'second']) == [
        ('second', 0.9),
        ('first', 0.1),
    ]
