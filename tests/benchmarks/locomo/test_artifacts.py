import json
from pathlib import Path

import pytest

from benchmarks.locomo.artifacts import (
    ArtifactStore,
    GraphCounts,
    RestartValidationError,
    graph_counts,
    validate_restart,
)
from benchmarks.locomo.retrieval import RetrievalResult


class _FakeDriver:
    def __init__(self) -> None:
        self.values = iter([3, 5, 7])

    async def execute_query(self, _: str, **__: object) -> tuple[list[dict], None, None]:
        return ([{'count': next(self.values)}], None, None)


@pytest.mark.asyncio
async def test_graph_counts_cover_episodes_entities_and_facts() -> None:
    assert await graph_counts(_FakeDriver()) == GraphCounts(episodes=3, entities=5, facts=7)


def test_artifact_store_does_not_create_accuracy_directory(tmp_path: Path) -> None:
    store = ArtifactStore.create(tmp_path)
    store.write_manifest({'kind': 'native-graphiti'})

    assert json.loads((tmp_path / 'manifest.json').read_text()) == {'kind': 'native-graphiti'}
    assert not (tmp_path / 'grades').exists()


def test_metrics_preserve_unknown_cost_when_provider_usage_is_incomplete(tmp_path: Path) -> None:
    store = ArtifactStore.create(tmp_path)
    trace_dir = tmp_path / 'trace'
    trace_dir.mkdir()
    event = {
        'logical_call_id': 'call-1',
        'attempt': 1,
        'prompt_name': 'extract_nodes.extract_message',
        'status': 'success',
        'model': 'deepseek-v4-flash',
        'latency_ms': 12.5,
        'usage': {'prompt_tokens': 10, 'completion_tokens': 2},
        'provider_cache': {'hit_tokens': None, 'miss_tokens': None},
        'estimated_cost_usd': None,
    }
    (trace_dir / 'events.jsonl').write_text(json.dumps(event) + '\n')

    summary = store.write_metrics()

    assert summary['llm_call_count'] == 1
    assert summary['estimated_cost_usd'] is None
    assert (tmp_path / 'metrics/llm_calls.csv').is_file()


def test_metrics_summarize_ingestion_and_retrieval_latency(tmp_path: Path) -> None:
    store = ArtifactStore.create(tmp_path)
    (tmp_path / 'ingestion/episodes.jsonl').write_text(
        json.dumps({'latency_ms': 10.25}) + '\n' + json.dumps({'latency_ms': 20.5}) + '\n'
    )
    (tmp_path / 'retrieval/before_restart.json').write_text(
        json.dumps({'node_latency_ms': 1.25, 'fact_latency_ms': 2.5})
    )
    (tmp_path / 'retrieval/after_restart.json').write_text(
        json.dumps({'node_latency_ms': 3.0, 'fact_latency_ms': 4.5})
    )

    summary = store.write_metrics()

    assert summary['ingestion_episode_count'] == 2
    assert summary['ingestion_latency_ms'] == 30.75
    assert summary['retrieval_before_restart_latency_ms'] == 3.75
    assert summary['retrieval_after_restart_latency_ms'] == 7.5


def _retrieval(node_ids: tuple[str, ...], fact_ids: tuple[str, ...]) -> RetrievalResult:
    return RetrievalResult(
        query='question',
        nodes=(),
        facts=(),
        node_ids=node_ids,
        fact_ids=fact_ids,
        node_latency_ms=1.0,
        fact_latency_ms=2.0,
    )


def test_restart_validation_compares_counts_ids_and_order() -> None:
    counts = GraphCounts(episodes=3, entities=5, facts=7)
    before = _retrieval(('n1', 'n2'), ('f1', 'f2'))

    validate_restart(counts, counts, before, _retrieval(('n1', 'n2'), ('f1', 'f2')))

    with pytest.raises(RestartValidationError, match='result IDs or order'):
        validate_restart(counts, counts, before, _retrieval(('n2', 'n1'), ('f1', 'f2')))
