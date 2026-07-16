"""Provider-boundary JSON trace for native Graphiti LLM calls."""

import json
import re
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from benchmarks.locomo.config import PricingSnapshot


@dataclass
class _ProviderCallState:
    logical_call_id: str
    prompt_name: str
    response_schema: dict[str, Any] | None
    attempt: int = 0


_PROVIDER_CALL: ContextVar[_ProviderCallState | None] = ContextVar(
    'locomo_provider_call', default=None
)


@contextmanager
def provider_call(
    prompt_name: str,
    response_schema: dict[str, Any] | None,
) -> Iterator[None]:
    """Associate Graphiti logical-call metadata with physical provider attempts."""
    state = _ProviderCallState(
        logical_call_id=str(uuid4()),
        prompt_name=prompt_name,
        response_schema=response_schema,
    )
    token = _PROVIDER_CALL.set(state)
    try:
        yield
    finally:
        _PROVIDER_CALL.reset(token)


class LLMTraceWriter:
    """Write complete request/output JSON and compact JSONL metadata."""

    def __init__(self, output_dir: Path, *, pricing: PricingSnapshot) -> None:
        self.output_dir = output_dir
        self.trace_dir = output_dir / 'trace'
        self.prompts_dir = self.trace_dir / 'prompts'
        self.outputs_dir = self.trace_dir / 'outputs'
        self.events_path = self.trace_dir / 'events.jsonl'
        self.pricing = pricing
        self._lock = threading.Lock()
        self.prompts_dir.mkdir(parents=True, exist_ok=True)
        self.outputs_dir.mkdir(parents=True, exist_ok=True)
        self._sequence = (
            sum(1 for line in self.events_path.read_text(encoding='utf-8').splitlines() if line)
            if self.events_path.exists()
            else 0
        )

    @property
    def event_count(self) -> int:
        """Return the number of provider attempts recorded by this writer."""
        with self._lock:
            return self._sequence

    def begin_attempt(self) -> tuple[int, _ProviderCallState]:
        """Allocate a stable sequence and advance the current logical attempt."""
        state = _PROVIDER_CALL.get()
        if state is None:
            state = _ProviderCallState(str(uuid4()), 'unattributed', None)
        state.attempt += 1
        with self._lock:
            self._sequence += 1
            return self._sequence, state

    def write_attempt(
        self,
        *,
        sequence: int,
        state: _ProviderCallState,
        request: dict[str, Any],
        response: object | None,
        latency_ms: float,
        error: BaseException | None,
    ) -> None:
        """Persist one physical provider attempt."""
        slug = _slugify(state.prompt_name)
        stem = f'{sequence:06d}-{slug}-{state.logical_call_id}-attempt-{state.attempt:02d}'
        request_path = self.prompts_dir / f'{stem}-prompt.json'
        output_path = self.outputs_dir / f'{stem}-raw.json'
        response_payload = _response_payload(response, error)
        _write_json(request_path, request)
        _write_json(output_path, response_payload)

        usage = response_payload.get('usage', {})
        cache_hit = _optional_int(usage.get('prompt_cache_hit_tokens'))
        cache_miss = _optional_int(usage.get('prompt_cache_miss_tokens'))
        completion = _optional_int(usage.get('completion_tokens'))
        cost = self.pricing.estimate_cost_usd(
            cache_hit_input_tokens=cache_hit,
            cache_miss_input_tokens=cache_miss,
            output_tokens=completion,
        )
        event = {
            'event_type': 'llm_call',
            'status': 'error' if error is not None else 'success',
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'logical_call_id': state.logical_call_id,
            'attempt': state.attempt,
            'prompt_name': state.prompt_name,
            'model': request.get('model'),
            'latency_ms': round(latency_ms, 3),
            'application_cache': 'disabled',
            'provider_cache': {'hit_tokens': cache_hit, 'miss_tokens': cache_miss},
            'usage': usage,
            'estimated_cost_usd': float(cost) if cost is not None else None,
            'finish_reason': response_payload.get('finish_reason'),
            'system_fingerprint': response_payload.get('system_fingerprint'),
            'request_artifact': str(request_path.relative_to(self.output_dir)),
            'output_artifact': str(output_path.relative_to(self.output_dir)),
            'error_type': type(error).__name__ if error is not None else None,
        }
        with self._lock, self.events_path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + '\n')


class TracedChatCompletions:
    """Proxy ``chat.completions`` and trace every physical ``create`` call."""

    def __init__(self, delegate: Any, writer: LLMTraceWriter) -> None:
        self._delegate = delegate
        self._writer = writer

    async def create(self, **kwargs: Any) -> Any:
        """Enable DeepSeek thinking, invoke the provider, and record the attempt."""
        call_kwargs = dict(kwargs)
        extra_body = dict(call_kwargs.get('extra_body') or {})
        extra_body['thinking'] = {'type': 'enabled'}
        call_kwargs['extra_body'] = extra_body

        sequence, state = self._writer.begin_attempt()
        request = {
            'logical_call_id': state.logical_call_id,
            'attempt': state.attempt,
            'prompt_name': state.prompt_name,
            'messages': _json_safe(call_kwargs.get('messages', [])),
            'model': call_kwargs.get('model'),
            'model_kwargs': _safe_model_kwargs(call_kwargs),
            'response_schema': _json_safe(state.response_schema),
            'application_cache': 'disabled',
        }
        started = perf_counter()
        try:
            response = await self._delegate.create(**call_kwargs)
        except BaseException as error:
            self._writer.write_attempt(
                sequence=sequence,
                state=state,
                request=request,
                response=None,
                latency_ms=(perf_counter() - started) * 1000,
                error=error,
            )
            raise
        self._writer.write_attempt(
            sequence=sequence,
            state=state,
            request=request,
            response=response,
            latency_ms=(perf_counter() - started) * 1000,
            error=None,
        )
        return response


def _safe_model_kwargs(kwargs: Mapping[str, Any]) -> dict[str, Any]:
    safe_keys = {'model', 'temperature', 'max_tokens', 'response_format', 'extra_body'}
    return {key: _json_safe(value) for key, value in kwargs.items() if key in safe_keys}


def _response_payload(response: object | None, error: BaseException | None) -> dict[str, Any]:
    if error is not None:
        return {'error': {'type': type(error).__name__, 'message': str(error)}, 'usage': {}}
    raw = _json_safe(response)
    if not isinstance(raw, dict):
        raw = {'raw_response': raw}
    choices = raw.get('choices') or []
    choice = choices[0] if choices and isinstance(choices[0], dict) else {}
    message = choice.get('message') if isinstance(choice, dict) else {}
    message = message if isinstance(message, dict) else {}
    return {
        'raw_response': raw,
        'content': message.get('content'),
        'reasoning_content': message.get('reasoning_content'),
        'finish_reason': choice.get('finish_reason') if isinstance(choice, dict) else None,
        'system_fingerprint': raw.get('system_fingerprint'),
        'usage': raw.get('usage') if isinstance(raw.get('usage'), dict) else {},
    }


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_json_safe(item) for item in value]
    model_dump = getattr(value, 'model_dump', None)
    if callable(model_dump):
        return _json_safe(model_dump(mode='json'))
    return repr(value)


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(_json_safe(payload), ensure_ascii=False, indent=2, allow_nan=False) + '\n',
        encoding='utf-8',
    )


def _slugify(value: str) -> str:
    return re.sub(r'[^a-zA-Z0-9_.-]+', '-', value).strip('-') or 'unattributed'


def _optional_int(value: object) -> int | None:
    return int(value) if isinstance(value, int) else None
