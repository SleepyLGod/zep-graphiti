"""Artifact, metric, state, and restart-validation helpers."""

import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from benchmarks.locomo.dataset import LocomoRow
from benchmarks.locomo.ingestion import IngestionRecord
from benchmarks.locomo.retrieval import RetrievalResult
from graphiti_core.driver.driver import GraphDriver


class RestartValidationError(RuntimeError):
    """Raised when persisted Neo4j state changes across a restart."""


@dataclass(frozen=True)
class GraphCounts:
    """Counts of the three graph structures materialized in Phase 1."""

    episodes: int
    entities: int
    facts: int

    def to_dict(self) -> dict[str, int]:
        """Return a JSON-safe count mapping."""
        return asdict(self)


class ArtifactStore:
    """Write benchmark evidence into separate input, trace, metric, and state areas."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir

    @classmethod
    def create(cls, output_dir: Path) -> 'ArtifactStore':
        """Create a new artifact root without overwriting an existing run."""
        if output_dir.exists() and any(output_dir.iterdir()):
            raise FileExistsError(f'Output directory is not empty: {output_dir}')
        output_dir.mkdir(parents=True, exist_ok=True)
        for name in ('input', 'ingestion', 'retrieval', 'metrics', 'state'):
            (output_dir / name).mkdir(exist_ok=True)
        return cls(output_dir)

    @classmethod
    def open_existing(cls, output_dir: Path) -> 'ArtifactStore':
        """Open an existing run that contains a state manifest."""
        if not (output_dir / 'state/neo4j.json').is_file():
            raise FileNotFoundError(f'Missing Neo4j state manifest under {output_dir}')
        return cls(output_dir)

    def write_manifest(self, payload: dict[str, Any]) -> None:
        """Write the immutable run contract."""
        _write_json(self.output_dir / 'manifest.json', payload)

    def write_input_rows(self, rows: list[LocomoRow]) -> None:
        """Write normalized input rows as JSONL."""
        _write_jsonl(self.output_dir / 'input/rows.jsonl', [row.to_dict() for row in rows])

    def write_ingestion(self, records: list[IngestionRecord]) -> None:
        """Write one record for each sequential episode insertion."""
        _write_jsonl(
            self.output_dir / 'ingestion/episodes.jsonl',
            [record.to_dict() for record in records],
        )

    def write_retrieval(self, stage: str, result: RetrievalResult) -> None:
        """Write ordered node/fact results for one retrieval stage."""
        if stage not in {'before_restart', 'after_restart'}:
            raise ValueError(f'Unsupported retrieval stage: {stage}')
        payload = result.to_dict()
        for rank, node in enumerate(payload['nodes'], start=1):
            node['rank'] = rank
        for rank, fact in enumerate(payload['facts'], start=1):
            fact['rank'] = rank
        _write_json(self.output_dir / f'retrieval/{stage}.json', payload)

    def write_state(self, payload: dict[str, Any]) -> None:
        """Write the Neo4j persistence manifest."""
        _write_json(self.output_dir / 'state/neo4j.json', payload)

    def read_state(self) -> dict[str, Any]:
        """Read the Neo4j persistence manifest."""
        return json.loads((self.output_dir / 'state/neo4j.json').read_text(encoding='utf-8'))

    def write_metrics(self) -> dict[str, Any]:
        """Derive compact latency, token, cache, and cost summaries from raw trace events."""
        events_path = self.output_dir / 'trace/events.jsonl'
        events = (
            [json.loads(line) for line in events_path.read_text(encoding='utf-8').splitlines()]
            if events_path.exists()
            else []
        )
        columns = [
            'logical_call_id',
            'attempt',
            'prompt_name',
            'status',
            'model',
            'latency_ms',
            'prompt_tokens',
            'cache_hit_tokens',
            'cache_miss_tokens',
            'completion_tokens',
            'reasoning_tokens',
            'estimated_cost_usd',
            'request_artifact',
            'output_artifact',
        ]
        rows = [_metric_row(event) for event in events]
        with (self.output_dir / 'metrics/llm_calls.csv').open(
            'w', encoding='utf-8', newline=''
        ) as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
        costs = [row['estimated_cost_usd'] for row in rows]
        ingestion_rows = _read_jsonl(self.output_dir / 'ingestion/episodes.jsonl')
        summary = {
            'ingestion_episode_count': len(ingestion_rows),
            'ingestion_latency_ms': round(
                sum(float(row.get('latency_ms') or 0) for row in ingestion_rows), 3
            ),
            'retrieval_before_restart_latency_ms': _retrieval_latency(
                self.output_dir / 'retrieval/before_restart.json'
            ),
            'retrieval_after_restart_latency_ms': _retrieval_latency(
                self.output_dir / 'retrieval/after_restart.json'
            ),
            'llm_call_count': len(rows),
            'llm_error_count': sum(row['status'] == 'error' for row in rows),
            'llm_latency_ms': round(sum(float(row['latency_ms']) for row in rows), 3),
            'prompt_tokens': sum(int(row['prompt_tokens'] or 0) for row in rows),
            'cache_hit_tokens': sum(int(row['cache_hit_tokens'] or 0) for row in rows),
            'cache_miss_tokens': sum(int(row['cache_miss_tokens'] or 0) for row in rows),
            'completion_tokens': sum(int(row['completion_tokens'] or 0) for row in rows),
            'reasoning_tokens': sum(int(row['reasoning_tokens'] or 0) for row in rows),
            'estimated_cost_usd': (
                round(sum(float(cost) for cost in costs), 12)
                if costs and all(cost is not None for cost in costs)
                else None
            ),
        }
        _write_json(self.output_dir / 'metrics/summary.json', summary)
        return summary


async def graph_counts(driver: GraphDriver) -> GraphCounts:
    """Read persisted episode, entity, and fact counts from Neo4j."""
    return GraphCounts(
        episodes=await _count(driver, 'MATCH (n:Episodic) RETURN count(n) AS count'),
        entities=await _count(driver, 'MATCH (n:Entity) RETURN count(n) AS count'),
        facts=await _count(driver, 'MATCH ()-[r:RELATES_TO]->() RETURN count(r) AS count'),
    )


def validate_restart(
    before_counts: GraphCounts,
    after_counts: GraphCounts,
    before_result: RetrievalResult,
    after_result: RetrievalResult,
) -> None:
    """Require state counts and ordered result identities to survive restart unchanged."""
    if before_counts != after_counts:
        raise RestartValidationError(
            f'Graph counts changed across restart: {before_counts} != {after_counts}'
        )
    if (
        before_result.node_ids != after_result.node_ids
        or before_result.fact_ids != after_result.fact_ids
    ):
        raise RestartValidationError('Retrieval result IDs or order changed across restart')


async def _count(driver: GraphDriver, query: str) -> int:
    records, _, _ = await driver.execute_query(query, routing_='r')
    return int(records[0]['count'])


def _metric_row(event: dict[str, Any]) -> dict[str, Any]:
    usage = event.get('usage') or {}
    completion_details = usage.get('completion_tokens_details') or {}
    provider_cache = event.get('provider_cache') or {}
    return {
        'logical_call_id': event.get('logical_call_id'),
        'attempt': event.get('attempt'),
        'prompt_name': event.get('prompt_name'),
        'status': event.get('status'),
        'model': event.get('model'),
        'latency_ms': event.get('latency_ms'),
        'prompt_tokens': usage.get('prompt_tokens'),
        'cache_hit_tokens': provider_cache.get('hit_tokens'),
        'cache_miss_tokens': provider_cache.get('miss_tokens'),
        'completion_tokens': usage.get('completion_tokens'),
        'reasoning_tokens': completion_details.get('reasoning_tokens'),
        'estimated_cost_usd': event.get('estimated_cost_usd'),
        'request_artifact': event.get('request_artifact'),
        'output_artifact': event.get('output_artifact'),
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line]


def _retrieval_latency(path: Path) -> float | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding='utf-8'))
    return round(
        float(payload.get('node_latency_ms') or 0) + float(payload.get('fact_latency_ms') or 0),
        3,
    )


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + '\n',
        encoding='utf-8',
    )


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        ''.join(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n' for row in rows),
        encoding='utf-8',
    )
