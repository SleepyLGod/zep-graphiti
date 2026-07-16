import json
from pathlib import Path

import pytest

from benchmarks.locomo.config import PricingSnapshot
from benchmarks.locomo.llm_trace import LLMTraceWriter, TracedChatCompletions, provider_call


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def model_dump(self, **_: object) -> dict:
        return self._payload


class _FakeCompletions:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.requests: list[dict] = []

    async def create(self, **kwargs: object) -> object:
        self.requests.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _response(*, include_cache_usage: bool = True) -> _FakeResponse:
    usage = {
        'prompt_tokens': 13,
        'completion_tokens': 7,
        'completion_tokens_details': {'reasoning_tokens': 4},
    }
    if include_cache_usage:
        usage.update({'prompt_cache_hit_tokens': 3, 'prompt_cache_miss_tokens': 10})
    return _FakeResponse(
        {
            'id': 'call-1',
            'model': 'deepseek-v4-flash',
            'system_fingerprint': 'fp',
            'choices': [
                {
                    'finish_reason': 'stop',
                    'message': {'content': '{"ok": true}', 'reasoning_content': 'reason'},
                }
            ],
            'usage': usage,
        }
    )


@pytest.mark.asyncio
async def test_provider_boundary_writes_json_only_artifacts(tmp_path: Path) -> None:
    writer = LLMTraceWriter(tmp_path, pricing=PricingSnapshot.deepseek_2026_07_17())
    delegate = _FakeCompletions([_response()])
    traced = TracedChatCompletions(delegate, writer)

    with provider_call('extract_edges.edge', {'type': 'object'}):
        await traced.create(
            model='deepseek-v4-flash',
            messages=[{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'user'}],
            response_format={'type': 'json_object'},
        )

    events = [
        json.loads(line) for line in (tmp_path / 'trace/events.jsonl').read_text().splitlines()
    ]
    assert len(events) == 1
    assert events[0]['prompt_name'] == 'extract_edges.edge'
    assert events[0]['application_cache'] == 'disabled'
    assert events[0]['provider_cache']['hit_tokens'] == 3
    assert events[0]['provider_cache']['miss_tokens'] == 10
    assert events[0]['estimated_cost_usd'] is not None
    assert events[0]['request_artifact'].endswith('.json')
    assert events[0]['output_artifact'].endswith('.json')
    assert not list((tmp_path / 'trace').rglob('*.txt'))

    request = json.loads((tmp_path / events[0]['request_artifact']).read_text())
    output = json.loads((tmp_path / events[0]['output_artifact']).read_text())
    assert request['messages'][0]['content'] == 'system'
    assert request['response_schema'] == {'type': 'object'}
    assert output['content'] == '{"ok": true}'
    assert output['reasoning_content'] == 'reason'


@pytest.mark.asyncio
async def test_each_physical_attempt_gets_an_event(tmp_path: Path) -> None:
    writer = LLMTraceWriter(tmp_path, pricing=PricingSnapshot.deepseek_2026_07_17())
    delegate = _FakeCompletions([RuntimeError('transient'), _response()])
    traced = TracedChatCompletions(delegate, writer)

    with provider_call('dedupe_edges.resolve_edge', None):
        with pytest.raises(RuntimeError, match='transient'):
            await traced.create(model='deepseek-v4-flash', messages=[])
        await traced.create(model='deepseek-v4-flash', messages=[])

    events = [
        json.loads(line) for line in (tmp_path / 'trace/events.jsonl').read_text().splitlines()
    ]
    assert [event['attempt'] for event in events] == [1, 2]
    assert [event['status'] for event in events] == ['error', 'success']
    assert events[0]['logical_call_id'] == events[1]['logical_call_id']


@pytest.mark.asyncio
async def test_missing_cache_usage_keeps_cost_null(tmp_path: Path) -> None:
    writer = LLMTraceWriter(tmp_path, pricing=PricingSnapshot.deepseek_2026_07_17())
    traced = TracedChatCompletions(_FakeCompletions([_response(include_cache_usage=False)]), writer)

    with provider_call('extract_nodes.extract_message', None):
        await traced.create(model='deepseek-v4-flash', messages=[])

    event = json.loads((tmp_path / 'trace/events.jsonl').read_text().splitlines()[0])
    assert event['estimated_cost_usd'] is None


@pytest.mark.asyncio
async def test_thinking_is_enabled_without_recording_secrets(tmp_path: Path) -> None:
    writer = LLMTraceWriter(tmp_path, pricing=PricingSnapshot.deepseek_2026_07_17())
    delegate = _FakeCompletions([_response()])
    traced = TracedChatCompletions(delegate, writer)

    with provider_call('extract_nodes.extract_message', None):
        await traced.create(
            model='deepseek-v4-flash',
            messages=[],
            extra_headers={'Authorization': 'Bearer secret'},
        )

    assert delegate.requests[0]['extra_body'] == {'thinking': {'type': 'enabled'}}
    event = json.loads((tmp_path / 'trace/events.jsonl').read_text().splitlines()[0])
    request_text = (tmp_path / event['request_artifact']).read_text()
    assert 'secret' not in request_text
    assert 'Authorization' not in request_text
