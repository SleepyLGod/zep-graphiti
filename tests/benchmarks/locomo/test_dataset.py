import json
from pathlib import Path

import pytest

from benchmarks.locomo.dataset import (
    LocomoDatasetError,
    load_rows,
    normalized_input_fingerprint,
    verify_dataset,
)


def _write_dataset(path: Path) -> None:
    path.write_text(
        json.dumps(
            [
                {
                    'sample_id': 'sample-0',
                    'conversation': {
                        'speaker_a': 'Caroline',
                        'speaker_b': 'Melanie',
                        'session_1_date_time': '1:14 pm on 25 May, 2023',
                        'session_1': [
                            {'speaker': 'Caroline', 'dia_id': 'D1:1', 'text': 'one'},
                            {'speaker': 'Melanie', 'dia_id': 'D1:2', 'text': 'two'},
                            {
                                'speaker': 'Caroline',
                                'dia_id': 'D1:3',
                                'text': 'three',
                                'blip_caption': 'an image',
                            },
                        ],
                    },
                }
            ]
        ),
        encoding='utf-8',
    )


def test_rows_use_one_based_numbers_and_preserve_local_time(tmp_path: Path) -> None:
    dataset = tmp_path / 'locomo.json'
    _write_dataset(dataset)

    rows = load_rows(dataset, sample_index=0, start_row=2, row_limit=2)

    assert [row.row_number for row in rows] == [2, 3]
    assert rows[0].episode_body == 'Melanie: two'
    assert rows[1].episode_body == 'Caroline: three\n(description of attached image: an image)'
    assert rows[0].reference_time.isoformat() == '2023-05-25T13:14:00'
    assert rows[0].reference_time.tzinfo is None
    assert rows[0].source_description == 'LOCOMO sample 0 session 1'


def test_input_fingerprint_is_stable_for_same_rows(tmp_path: Path) -> None:
    dataset = tmp_path / 'locomo.json'
    _write_dataset(dataset)
    rows = load_rows(dataset, sample_index=0, start_row=1, row_limit=3)

    assert normalized_input_fingerprint(rows) == normalized_input_fingerprint(list(rows))


def test_dataset_hash_mismatch_is_rejected(tmp_path: Path) -> None:
    dataset = tmp_path / 'locomo.json'
    dataset.write_bytes(b'not the pinned dataset')

    with pytest.raises(LocomoDatasetError, match='SHA-256'):
        verify_dataset(dataset, expected_sha256='0' * 64)
