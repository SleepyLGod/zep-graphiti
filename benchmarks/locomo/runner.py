"""CLI runner for pinned native Graphiti LOCOMO ingestion and restart smoke."""

import argparse
import asyncio
import os
import re
import socket
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic, sleep
from typing import Any, Protocol

from benchmarks.locomo.artifacts import (
    ArtifactStore,
    GraphCounts,
    graph_counts,
    validate_restart,
)
from benchmarks.locomo.clients import BgeM3Embedder, CpuBgeReranker, build_llm_client
from benchmarks.locomo.config import (
    BGE_EMBEDDING_MODEL,
    BGE_RERANKER_MODEL,
    DEEPSEEK_MODEL,
    DEFAULT_QUERY,
    DEFAULT_ROW_LIMIT,
    DEFAULT_SAMPLE_INDEX,
    DEFAULT_START_ROW,
    GRAPHITI_COMMIT,
    LOCOMO_COMMIT,
    LOCOMO_SHA256,
    LOCOMO_URL,
    NEO4J_IMAGE,
    PricingSnapshot,
)
from benchmarks.locomo.dataset import (
    LocomoRow,
    download_dataset,
    load_rows,
    normalized_input_fingerprint,
    verify_dataset,
)
from benchmarks.locomo.ingestion import ingest_rows
from benchmarks.locomo.llm_trace import LLMTraceWriter
from benchmarks.locomo.retrieval import RetrievalResult, retrieve
from graphiti_core import Graphiti


class RunnerError(RuntimeError):
    """Raised when the benchmark environment violates its fixed contract."""


class CommandResult(Protocol):
    """Subset of ``CompletedProcess`` used by the Compose controller."""

    returncode: int
    stdout: str
    stderr: str


CommandRunner = Callable[[list[str]], CommandResult]


@dataclass(frozen=True)
class RunConfig:
    """Resolved CLI configuration for one native baseline run."""

    command: str
    repository: Path
    output_dir: Path
    compose_project: str
    dataset_path: Path | None
    sample_index: int
    start_row: int
    row_limit: int
    query: str
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: str


class DockerComposeController:
    """Manage only the benchmark's dedicated Neo4j Compose service and volume."""

    def __init__(
        self,
        *,
        repository: Path,
        project: str,
        run_command: CommandRunner | None = None,
    ) -> None:
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', project):
            raise RunnerError(f'Invalid Compose project name: {project}')
        self.repository = repository
        self.project = project
        self.volume = f'{project}_neo4j_data'
        self._run_command = run_command or self._run

    def require_new_volume(self) -> None:
        """Reject a project whose persistent volume already exists."""
        result = self._run_command(['docker', 'volume', 'inspect', self.volume])
        if result.returncode == 0:
            raise RunnerError(f'Neo4j volume already exists: {self.volume}')
        if result.returncode != 1:
            raise RunnerError(f'Unable to inspect Neo4j volume: {result.stderr.strip()}')

    def start_new(self) -> None:
        """Start a fresh Neo4j service and wait for its health check."""
        self._checked(self._compose('up', '-d', '--wait', 'neo4j'))

    def stop(self) -> None:
        """Stop Neo4j while preserving its container and named volume."""
        self._checked(self._compose('stop', 'neo4j'))

    def start_existing(self) -> None:
        """Restart the existing Neo4j container without recreating its volume."""
        self._checked(self._compose('start', 'neo4j'))

    def wait_until_healthy(self, timeout_seconds: int = 60) -> None:
        """Poll the Compose service until Docker reports a healthy container."""
        deadline = monotonic() + timeout_seconds
        while True:
            container = self._container_id()
            result = self._run_command(
                ['docker', 'inspect', '--format', '{{.State.Health.Status}}', container]
            )
            if result.returncode == 0 and result.stdout.strip() == 'healthy':
                return
            if monotonic() >= deadline:
                raise RunnerError('Neo4j did not become healthy after restart')
            sleep(1)

    def image_digest(self) -> str:
        """Return the immutable image ID used by the running Neo4j container."""
        result = self._checked(
            ['docker', 'inspect', '--format', '{{.Image}}', self._container_id()]
        )
        return result.stdout.strip()

    def _container_id(self) -> str:
        result = self._checked(self._compose('ps', '-q', 'neo4j'))
        container = result.stdout.strip()
        if not container:
            raise RunnerError('Compose did not return a Neo4j container ID')
        return container

    def _compose(self, *args: str) -> list[str]:
        return [
            'docker',
            'compose',
            '--project-directory',
            str(self.repository),
            '-f',
            str(self.repository / 'docker-compose.yml'),
            '-p',
            self.project,
            *args,
        ]

    def _checked(self, command: list[str]) -> CommandResult:
        result = self._run_command(command)
        if result.returncode != 0:
            raise RunnerError(f'Command failed: {" ".join(command)}\n{result.stderr.strip()}')
        return result

    def _run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            cwd=self.repository,
            text=True,
            capture_output=True,
            check=False,
        )


async def run_restart_smoke(config: RunConfig) -> None:
    """Ingest, retrieve, restart Neo4j, and validate retrieval without re-ingestion."""
    store = ArtifactStore.create(config.output_dir)
    rows = _prepare_input(store, config)
    input_fingerprint = normalized_input_fingerprint(rows)
    pricing = PricingSnapshot.deepseek_2026_07_17()
    trace_writer = LLMTraceWriter(config.output_dir, pricing=pricing)
    docker = DockerComposeController(repository=config.repository, project=config.compose_project)
    docker.require_new_volume()
    _require_ports_available(7474, 7687)
    _write_manifest(store, config, input_fingerprint, pricing)

    llm_client = build_llm_client(api_key=_deepseek_api_key(), trace_writer=trace_writer)
    embedder = BgeM3Embedder()
    reranker = CpuBgeReranker()
    before_counts: GraphCounts | None = None
    before_result: RetrievalResult | None = None
    image_digest: str | None = None
    try:
        docker.start_new()
        image_digest = docker.image_digest()
        graphiti = _graphiti(config, llm_client, embedder, reranker)
        try:
            await graphiti.build_indices_and_constraints()
            ingestion = await ingest_rows(graphiti, rows)
            store.write_ingestion(ingestion)
            before_counts = await graph_counts(graphiti.driver)
            before_result = await _retrieve_without_llm(graphiti, config.query, trace_writer)
            store.write_retrieval('before_restart', before_result)
            store.write_state(
                _state_payload(
                    config,
                    input_fingerprint,
                    image_digest,
                    before_counts,
                    restart_validated=False,
                )
            )
        finally:
            await graphiti.close()

        docker.stop()
        docker.start_existing()
        docker.wait_until_healthy()

        restored = _graphiti(config, llm_client, embedder, reranker)
        try:
            after_counts = await graph_counts(restored.driver)
            after_result = await _retrieve_without_llm(restored, config.query, trace_writer)
            store.write_retrieval('after_restart', after_result)
            validate_restart(before_counts, after_counts, before_result, after_result)
            store.write_state(
                _state_payload(
                    config,
                    input_fingerprint,
                    image_digest,
                    after_counts,
                    restart_validated=True,
                )
            )
        finally:
            await restored.close()
    finally:
        store.write_metrics()
        _stop_preserving_volume(docker)
        await llm_client.client.close()


async def run_ingestion_and_retrieval(config: RunConfig) -> None:
    """Run ingestion and retrieval against an already running Neo4j instance."""
    store = ArtifactStore.create(config.output_dir)
    rows = _prepare_input(store, config)
    input_fingerprint = normalized_input_fingerprint(rows)
    pricing = PricingSnapshot.deepseek_2026_07_17()
    trace_writer = LLMTraceWriter(config.output_dir, pricing=pricing)
    _write_manifest(store, config, input_fingerprint, pricing)
    llm_client = build_llm_client(api_key=_deepseek_api_key(), trace_writer=trace_writer)
    graphiti = _graphiti(config, llm_client, BgeM3Embedder(), CpuBgeReranker())
    try:
        await graphiti.build_indices_and_constraints()
        store.write_ingestion(await ingest_rows(graphiti, rows))
        counts = await graph_counts(graphiti.driver)
        result = await _retrieve_without_llm(graphiti, config.query, trace_writer)
        store.write_retrieval('before_restart', result)
        store.write_state(
            _state_payload(config, input_fingerprint, None, counts, restart_validated=False)
        )
    finally:
        store.write_metrics()
        await graphiti.close()
        await llm_client.client.close()


async def run_retrieval_only(config: RunConfig) -> None:
    """Restart persisted benchmark Neo4j state and retrieve without ingestion."""
    store = ArtifactStore.open_existing(config.output_dir)
    state = store.read_state()
    _validate_state_contract(state, config)
    pricing = PricingSnapshot.deepseek_2026_07_17()
    trace_writer = LLMTraceWriter(config.output_dir, pricing=pricing)
    docker = DockerComposeController(repository=config.repository, project=state['compose_project'])
    llm_client = build_llm_client(api_key=_deepseek_api_key(), trace_writer=trace_writer)
    try:
        docker.start_existing()
        docker.wait_until_healthy()
        graphiti = _graphiti(config, llm_client, BgeM3Embedder(), CpuBgeReranker())
        try:
            result = await _retrieve_without_llm(graphiti, config.query, trace_writer)
            store.write_retrieval('after_restart', result)
        finally:
            await graphiti.close()
    finally:
        store.write_metrics()
        _stop_preserving_volume(docker)
        await llm_client.client.close()


def parse_args(argv: Sequence[str] | None = None) -> RunConfig:
    """Parse the three explicit native-baseline execution modes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('run', 'retrieval-only', 'restart-smoke'))
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--compose-project')
    parser.add_argument('--dataset-path', type=Path)
    parser.add_argument('--sample-index', type=int, default=DEFAULT_SAMPLE_INDEX)
    parser.add_argument('--start-row', type=int, default=DEFAULT_START_ROW)
    parser.add_argument('--row-limit', type=int, default=DEFAULT_ROW_LIMIT)
    parser.add_argument('--query', default=DEFAULT_QUERY)
    parser.add_argument('--neo4j-uri', default='bolt://localhost:7687')
    parser.add_argument('--neo4j-user', default=os.getenv('NEO4J_USER', 'neo4j'))
    parser.add_argument('--neo4j-password', default=os.getenv('NEO4J_PASSWORD', 'password'))
    args = parser.parse_args(argv)
    repository = Path(__file__).resolve().parents[2]
    project = args.compose_project or _default_project(args.output_dir)
    return RunConfig(
        command=args.command,
        repository=repository,
        output_dir=args.output_dir.resolve(),
        compose_project=project,
        dataset_path=args.dataset_path.resolve() if args.dataset_path else None,
        sample_index=args.sample_index,
        start_row=args.start_row,
        row_limit=args.row_limit,
        query=args.query,
        neo4j_uri=args.neo4j_uri,
        neo4j_user=args.neo4j_user,
        neo4j_password=args.neo4j_password,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Execute the selected native baseline mode."""
    config = parse_args(argv)
    try:
        if config.command == 'restart-smoke':
            asyncio.run(run_restart_smoke(config))
        elif config.command == 'retrieval-only':
            asyncio.run(run_retrieval_only(config))
        else:
            asyncio.run(run_ingestion_and_retrieval(config))
    except Exception as error:
        print(f'Native Graphiti LOCOMO run failed: {error}', file=sys.stderr)
        return 1
    return 0


def _prepare_input(store: ArtifactStore, config: RunConfig) -> list[LocomoRow]:
    dataset_path = config.dataset_path or store.output_dir / 'input/locomo10.json'
    if config.dataset_path is None:
        download_dataset(dataset_path)
    else:
        verify_dataset(dataset_path)
    rows = load_rows(
        dataset_path,
        sample_index=config.sample_index,
        start_row=config.start_row,
        row_limit=config.row_limit,
    )
    store.write_input_rows(rows)
    return rows


def _write_manifest(
    store: ArtifactStore,
    config: RunConfig,
    input_fingerprint: str,
    pricing: PricingSnapshot,
) -> None:
    store.write_manifest(
        {
            'kind': 'native-graphiti-locomo-phase1',
            'created_at': datetime.now(timezone.utc).isoformat(),
            'graphiti_commit': GRAPHITI_COMMIT,
            'locomo': {
                'commit': LOCOMO_COMMIT,
                'sha256': LOCOMO_SHA256,
                'source_url': LOCOMO_URL,
                'sample_index': config.sample_index,
                'start_row': config.start_row,
                'row_limit': config.row_limit,
                'input_fingerprint': input_fingerprint,
            },
            'models': {
                'llm': DEEPSEEK_MODEL,
                'embedding': BGE_EMBEDDING_MODEL,
                'reranker': BGE_RERANKER_MODEL,
                'embedding_device': 'cpu',
                'reranker_device': 'cpu',
            },
            'query': config.query,
            'neo4j_image': NEO4J_IMAGE,
            'application_cache': 'disabled',
            'provider_thinking': 'enabled',
            'pricing': pricing.to_dict(),
            'accuracy': None,
        }
    )


def _graphiti(
    config: RunConfig,
    llm_client: Any,
    embedder: BgeM3Embedder,
    reranker: CpuBgeReranker,
) -> Graphiti:
    return Graphiti(
        uri=config.neo4j_uri,
        user=config.neo4j_user,
        password=config.neo4j_password,
        llm_client=llm_client,
        embedder=embedder,
        cross_encoder=reranker,
    )


async def _retrieve_without_llm(
    graphiti: Graphiti,
    query: str,
    trace_writer: LLMTraceWriter,
) -> RetrievalResult:
    before = trace_writer.event_count
    result = await retrieve(graphiti, query)
    if trace_writer.event_count != before:
        raise RunnerError('Retrieval made an unexpected generative LLM call')
    return result


def _state_payload(
    config: RunConfig,
    input_fingerprint: str,
    image_digest: str | None,
    counts: GraphCounts,
    *,
    restart_validated: bool,
) -> dict[str, Any]:
    return {
        'compose_project': config.compose_project,
        'volume': f'{config.compose_project}_neo4j_data',
        'database': 'neo4j',
        'neo4j_image': NEO4J_IMAGE,
        'neo4j_image_digest': image_digest,
        'graphiti_commit': GRAPHITI_COMMIT,
        'input_fingerprint': input_fingerprint,
        'counts': counts.to_dict(),
        'restart_validated': restart_validated,
        'updated_at': datetime.now(timezone.utc).isoformat(),
    }


def _validate_state_contract(state: dict[str, Any], config: RunConfig) -> None:
    if state.get('graphiti_commit') != GRAPHITI_COMMIT:
        raise RunnerError('State manifest Graphiti commit does not match this harness')
    if state.get('compose_project') != config.compose_project:
        raise RunnerError('State manifest Compose project does not match the requested project')


def _deepseek_api_key() -> str:
    api_key = os.getenv('DEEPSEEK_API_KEY', '')
    if not api_key:
        raise RunnerError('DEEPSEEK_API_KEY is required')
    return api_key


def _default_project(output_dir: Path) -> str:
    slug = re.sub(r'[^a-z0-9_-]+', '-', output_dir.name.lower()).strip('-_')
    if slug and slug[0].isalnum():
        return f'graphiti-{slug}'[:63].rstrip('-_')
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')
    return f'graphiti-locomo-{timestamp}'


def _require_ports_available(*ports: int) -> None:
    for port in ports:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
            if connection.connect_ex(('127.0.0.1', port)) == 0:
                raise RunnerError(f'Required localhost port is already in use: {port}')


def _stop_preserving_volume(docker: DockerComposeController) -> None:
    try:
        docker.stop()
    except RunnerError as error:
        print(f'Warning: unable to stop Neo4j cleanly: {error}', file=sys.stderr)


if __name__ == '__main__':
    raise SystemExit(main())
