from datetime import datetime
from types import SimpleNamespace

import pytest

from benchmarks.locomo.dataset import LocomoRow
from benchmarks.locomo.ingestion import ingest_rows
from graphiti_core.nodes import EpisodeType


def _row(number: int) -> LocomoRow:
    return LocomoRow(
        sample_index=0,
        sample_id='sample-0',
        row_number=number,
        session_number=2,
        dia_id=f'D2:{number}',
        speaker='Caroline',
        text=f'message {number}',
        blip_caption=None,
        episode_body=f'Caroline: message {number}',
        source_description='LOCOMO sample 0 session 2',
        reference_time=datetime(2023, 5, 25, 13, 14),
    )


class _FakeGraphiti:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def add_episode(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        row_number = len(self.calls)
        return SimpleNamespace(
            episode=SimpleNamespace(uuid=f'episode-{row_number}'),
            nodes=[SimpleNamespace(uuid=f'node-{row_number}')],
            edges=[SimpleNamespace(uuid=f'fact-{row_number}')],
        )


@pytest.mark.asyncio
async def test_ingestion_is_sequential_and_uses_native_add_episode_contract() -> None:
    graphiti = _FakeGraphiti()

    records = await ingest_rows(graphiti, [_row(26), _row(27)])

    assert [record.episode_uuid for record in records] == ['episode-1', 'episode-2']
    assert [call['name'] for call in graphiti.calls] == [
        'locomo-s0-row-000026',
        'locomo-s0-row-000027',
    ]
    assert all(call['source'] is EpisodeType.message for call in graphiti.calls)
    assert all(call['group_id'] is None for call in graphiti.calls)
    assert all(call['update_communities'] is False for call in graphiti.calls)
