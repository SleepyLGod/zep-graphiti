"""Download, verify, and normalize the pinned LOCOMO input."""

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from benchmarks.locomo.config import LOCOMO_SHA256, LOCOMO_URL


class LocomoDatasetError(ValueError):
    """Raised when pinned LOCOMO data is missing or malformed."""


@dataclass(frozen=True)
class LocomoRow:
    """One normalized message row from a LOCOMO conversation."""

    sample_index: int
    sample_id: str
    row_number: int
    session_number: int
    dia_id: str
    speaker: str
    text: str
    blip_caption: str | None
    episode_body: str
    source_description: str
    reference_time: datetime

    @property
    def episode_name(self) -> str:
        """Return a deterministic human-readable episode name."""
        return f'locomo-s{self.sample_index}-row-{self.row_number:06d}'

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation for benchmark artifacts."""
        payload = asdict(self)
        payload['reference_time'] = self.reference_time.isoformat()
        payload['episode_name'] = self.episode_name
        return payload


def download_dataset(
    destination: Path,
    *,
    url: str = LOCOMO_URL,
    expected_sha256: str = LOCOMO_SHA256,
) -> Path:
    """Download the pinned dataset once and verify it before writing to disk."""
    if destination.exists():
        verify_dataset(destination, expected_sha256=expected_sha256)
        return destination
    with urlopen(url) as response:  # noqa: S310 - URL is pinned by the benchmark configuration.
        content = response.read()
    actual_sha256 = hashlib.sha256(content).hexdigest()
    if actual_sha256 != expected_sha256:
        raise LocomoDatasetError(
            f'LOCOMO SHA-256 mismatch: expected {expected_sha256}, got {actual_sha256}'
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    return destination


def verify_dataset(path: Path, *, expected_sha256: str = LOCOMO_SHA256) -> None:
    """Reject any dataset whose bytes do not match the pinned SHA-256."""
    actual_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual_sha256 != expected_sha256:
        raise LocomoDatasetError(
            f'LOCOMO SHA-256 mismatch: expected {expected_sha256}, got {actual_sha256}'
        )


def load_rows(
    path: Path,
    *,
    sample_index: int,
    start_row: int,
    row_limit: int,
) -> list[LocomoRow]:
    """Load a one-based contiguous row range from one LOCOMO sample."""
    if start_row < 1:
        raise LocomoDatasetError('start_row must be one-based and at least 1')
    if row_limit < 1:
        raise LocomoDatasetError('row_limit must be at least 1')
    raw_samples = json.loads(path.read_text(encoding='utf-8'))
    try:
        sample = raw_samples[sample_index]
        conversation = sample['conversation']
    except (IndexError, KeyError, TypeError) as error:
        raise LocomoDatasetError(f'Invalid LOCOMO sample index: {sample_index}') from error

    rows: list[LocomoRow] = []
    row_number = 0
    session_numbers = sorted(
        int(key.removeprefix('session_'))
        for key, value in conversation.items()
        if key.startswith('session_')
        and key.removeprefix('session_').isdigit()
        and isinstance(value, list)
    )
    for session_number in session_numbers:
        messages = conversation[f'session_{session_number}']
        reference_time = _parse_reference_time(
            conversation.get(f'session_{session_number}_date_time'), session_number
        )
        for message in messages:
            row_number += 1
            if row_number < start_row or row_number >= start_row + row_limit:
                continue
            rows.append(
                _normalize_message(
                    message,
                    sample_index=sample_index,
                    sample_id=str(sample.get('sample_id', sample_index)),
                    row_number=row_number,
                    session_number=session_number,
                    reference_time=reference_time,
                )
            )

    if len(rows) != row_limit:
        raise LocomoDatasetError(
            f'Requested rows {start_row}-{start_row + row_limit - 1}, found {len(rows)} rows'
        )
    return rows


def normalized_input_fingerprint(rows: list[LocomoRow]) -> str:
    """Hash normalized rows using canonical JSON serialization."""
    payload = json.dumps(
        [row.to_dict() for row in rows],
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
    ).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def _parse_reference_time(value: object, session_number: int) -> datetime:
    if not isinstance(value, str):
        raise LocomoDatasetError(f'Missing local datetime for LOCOMO session {session_number}')
    try:
        return datetime.strptime(value, '%I:%M %p on %d %B, %Y')
    except ValueError as error:
        raise LocomoDatasetError(f'Invalid LOCOMO local datetime: {value}') from error


def _normalize_message(
    message: object,
    *,
    sample_index: int,
    sample_id: str,
    row_number: int,
    session_number: int,
    reference_time: datetime,
) -> LocomoRow:
    if not isinstance(message, dict):
        raise LocomoDatasetError(f'LOCOMO row {row_number} is not an object')
    speaker = message.get('speaker')
    text = message.get('text')
    if not isinstance(speaker, str) or not isinstance(text, str):
        raise LocomoDatasetError(f'LOCOMO row {row_number} lacks speaker or text')
    caption_value = message.get('blip_caption')
    caption = (
        caption_value.strip() if isinstance(caption_value, str) and caption_value.strip() else None
    )
    episode_body = f'{speaker}: {text}'
    if caption is not None:
        episode_body += f'\n(description of attached image: {caption})'
    return LocomoRow(
        sample_index=sample_index,
        sample_id=sample_id,
        row_number=row_number,
        session_number=session_number,
        dia_id=str(message.get('dia_id', '')),
        speaker=speaker,
        text=text,
        blip_caption=caption,
        episode_body=episode_body,
        source_description=f'LOCOMO sample {sample_index} session {session_number}',
        reference_time=reference_time,
    )
