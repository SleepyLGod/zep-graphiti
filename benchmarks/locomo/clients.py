"""Benchmark-local DeepSeek, BGE embedder, and BGE reranker clients."""

import asyncio
from collections.abc import Iterable
from types import SimpleNamespace
from typing import Any

from openai import AsyncOpenAI
from pydantic import BaseModel

from benchmarks.locomo.config import (
    BGE_EMBEDDING_DIM,
    BGE_EMBEDDING_MODEL,
    BGE_RERANKER_MODEL,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MAX_TOKENS,
    DEEPSEEK_MODEL,
    DEEPSEEK_TEMPERATURE,
)
from benchmarks.locomo.llm_trace import LLMTraceWriter, TracedChatCompletions, provider_call
from graphiti_core.cross_encoder.client import CrossEncoderClient
from graphiti_core.embedder.client import EmbedderClient, EmbedderConfig
from graphiti_core.llm_client.config import LLMConfig, ModelSize
from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
from graphiti_core.prompts.models import Message


class BgeM3Embedder(EmbedderClient):
    """CPU-only BGE-M3 embedder with normalized 1024-dimensional vectors."""

    def __init__(self, *, model: Any | None = None, embedding_dim: int = BGE_EMBEDDING_DIM) -> None:
        self.config = EmbedderConfig(embedding_dim=embedding_dim)
        self.model = model if model is not None else _load_embedding_model()

    async def create(
        self, input_data: str | list[str] | Iterable[int] | Iterable[Iterable[int]]
    ) -> list[float]:
        """Embed one string using the Graphiti embedder contract."""
        if not isinstance(input_data, str):
            raise TypeError('BgeM3Embedder.create expects one string')
        vectors = await self._encode([input_data])
        return vectors[0]

    async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
        """Embed a list of strings in one CPU model call."""
        return await self._encode(input_data_list)

    async def _encode(self, inputs: list[str]) -> list[list[float]]:
        encoded = await asyncio.to_thread(
            self.model.encode,
            inputs,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return [[float(value) for value in row[: self.config.embedding_dim]] for row in encoded]


class CpuBgeReranker(CrossEncoderClient):
    """CPU-only BGE reranker implementing Graphiti's cross-encoder contract."""

    def __init__(self, *, model: Any | None = None) -> None:
        self.model = model if model is not None else _load_reranker_model()

    async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
        """Return passages sorted by descending cross-encoder score."""
        if not passages:
            return []
        pairs = [[query, passage] for passage in passages]
        scores = await asyncio.to_thread(self.model.predict, pairs)
        return sorted(
            [(passage, float(score)) for passage, score in zip(passages, scores, strict=True)],
            key=lambda item: item[1],
            reverse=True,
        )


class TracedOpenAIGenericClient(OpenAIGenericClient):
    """Attach Graphiti prompt metadata to provider-boundary trace events."""

    async def generate_response(
        self,
        messages: list[Message],
        response_model: type[BaseModel] | None = None,
        max_tokens: int | None = None,
        model_size: ModelSize = ModelSize.medium,
        group_id: str | None = None,
        prompt_name: str | None = None,
        *,
        attribute_extraction: bool = False,
    ) -> dict[str, Any]:
        """Delegate unchanged Graphiti behavior inside a trace correlation context."""
        response_schema = response_model.model_json_schema() if response_model is not None else None
        with provider_call(prompt_name or 'unattributed', response_schema):
            return await super().generate_response(
                messages,
                response_model=response_model,
                max_tokens=max_tokens,
                model_size=model_size,
                group_id=group_id,
                prompt_name=prompt_name,
                attribute_extraction=attribute_extraction,
            )


class _TracedAsyncOpenAI:
    def __init__(self, client: AsyncOpenAI, trace_writer: LLMTraceWriter) -> None:
        self._client = client
        self.chat = SimpleNamespace(
            completions=TracedChatCompletions(client.chat.completions, trace_writer)
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


def build_llm_client(*, api_key: str, trace_writer: LLMTraceWriter) -> TracedOpenAIGenericClient:
    """Build the fixed DeepSeek client without enabling Graphiti's application cache."""
    if not api_key:
        raise ValueError('DEEPSEEK_API_KEY is required')
    openai_client = AsyncOpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL)
    traced_client = _TracedAsyncOpenAI(openai_client, trace_writer)
    return TracedOpenAIGenericClient(
        config=LLMConfig(
            api_key=api_key,
            model=DEEPSEEK_MODEL,
            small_model=DEEPSEEK_MODEL,
            base_url=DEEPSEEK_BASE_URL,
            temperature=DEEPSEEK_TEMPERATURE,
            max_tokens=DEEPSEEK_MAX_TOKENS,
        ),
        cache=False,
        client=traced_client,
        max_tokens=DEEPSEEK_MAX_TOKENS,
        structured_output_mode='json_object',
    )


def _load_embedding_model() -> Any:
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(BGE_EMBEDDING_MODEL, device='cpu')


def _load_reranker_model() -> Any:
    from sentence_transformers import CrossEncoder

    return CrossEncoder(BGE_RERANKER_MODEL, device='cpu')
