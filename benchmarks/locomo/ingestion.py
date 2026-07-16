"""Sequential native Graphiti ingestion for normalized LOCOMO rows."""

from dataclasses import asdict, dataclass
from time import perf_counter
from typing import Any

from benchmarks.locomo.dataset import LocomoRow
from graphiti_core import Graphiti
from graphiti_core.nodes import EpisodeType


@dataclass(frozen=True)
class IngestionRecord:
    """Observed output and latency for one native ``add_episode`` call."""

    row_number: int
    episode_uuid: str
    node_uuids: tuple[str, ...]
    fact_uuids: tuple[str, ...]
    latency_ms: float

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe artifact record."""
        return asdict(self)


async def ingest_rows(
    graphiti: Graphiti,
    rows: list[LocomoRow],
) -> list[IngestionRecord]:
    """Await each native Graphiti episode insertion in source order."""
    records: list[IngestionRecord] = []
    for row in rows:
        started = perf_counter()
        result = await graphiti.add_episode(
            name=row.episode_name,
            episode_body=row.episode_body,
            source_description=row.source_description,
            reference_time=row.reference_time,
            source=EpisodeType.message,
            group_id=None,
            update_communities=False,
        )
        records.append(
            IngestionRecord(
                row_number=row.row_number,
                episode_uuid=str(result.episode.uuid),
                node_uuids=tuple(str(node.uuid) for node in result.nodes),
                fact_uuids=tuple(str(edge.uuid) for edge in result.edges),
                latency_ms=round((perf_counter() - started) * 1000, 3),
            )
        )
    return records
