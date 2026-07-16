# Graphiti and agent-memory prompt parity audit

## Scope

This audit compares the native Graphiti ingestion calls observed in the Phase 1
LOCOMO smoke with the declarative Zep policy in agent-memory. It does not infer
prompt coverage from function names alone.

- Graphiti source: `5e2be0faf7038a5b40e700d757b2c337e96b3a05`
- Native harness: `0247252`
- Agent-memory policy: `a601396bff6ae929a9f21608362fc1778ea2d321`
- Evidence run: `native-graphiti-phase1-20260717-032647`
- Input: LOCOMO sample 0, one-based rows 26-28
- Communities: disabled

The evidence run recorded nine successful provider calls and no retries:

| Graphiti prompt | Calls | Agent-memory counterpart | Status |
| --- | ---: | --- | --- |
| `extract_nodes.extract_message` | 3 | `_ENTITY_EXTRACT_INSTRUCTION` through `sem_flat_map` | adapted |
| `dedupe_nodes.nodes` | 1 | `_ENTITY_GROUP_INSTRUCTION` through `sem_groupby` | adapted |
| `extract_edges.edge` | 3 | `_FACT_EXTRACT_INSTRUCTION` through `sem_flat_map` | adapted |
| `dedupe_edges.resolve_edge` | 2 | `_FACT_GROUP_INSTRUCTION` plus `_CONTRADICTORY_FACT_INSTRUCTION` | adapted |

Every provider call in this run is accounted for. The parity gate is **not yet
passed**: none of the four observed prompt contracts is an exact copy at the
operator boundary, and the material differences below must be resolved before
the full comparison benchmark.

## What the trace contains

The request artifact is the actual provider-boundary request, not only the
prompt-library function output. Each request contains:

1. the Graphiti prompt-library system and user messages;
2. `Do not escape unicode characters` from `prompt_helpers.py`;
3. the language-preservation instruction from `llm_client/client.py`;
4. the Pydantic response schema supplied separately to the JSON-object client.

Agent-memory uses LOTUS operators with their own formatter and system prompt.
Copying Graphiti's complete provider-boundary system message into an operator
instruction would therefore duplicate responsibilities. Parity must preserve
the task constraints and output contract while respecting each operator's real
boundary.

## Observed prompt mappings

### Entity extraction

Native Graphiti uses
[`extract_nodes.extract_message`](../../graphiti_core/prompts/extract_nodes.py).
The policy preserves the main dataflow contract: extract entities from the
current episode, use previous episodes only for reference resolution, avoid
pronoun names, and emit one row per entity.

The policy currently omits material native constraints:

- speaker-first extraction;
- the detailed exclusions for generic nouns, activities, media, feelings, and
  unqualified kinship or pet references;
- the specificity rules and examples that prevent bare or context-dependent
  entity names;
- the exact entity-type ID response contract;
- the provider-boundary language and Unicode instructions.

The `entity_type_id` to `entity_type` change is an intentional schema adaptation.
The omitted extraction constraints are **missing parity**, not an intentional
semantic change.

### Entity resolution

Native Graphiti uses
[`dedupe_nodes.nodes`](../../graphiti_core/prompts/dedupe_nodes.py) after candidate
retrieval. It returns exactly one resolution per extracted entity and chooses a
single `duplicate_candidate_id`, or `-1` when no candidate is sufficiently
certain.

Agent-memory's `sem_groupby` keeps the core same-real-world-entity criterion,
but it performs semantic grouping rather than candidate-scoped top-1 record
linkage. It does not preserve these native contracts:

- one result for every extracted entity;
- selection of one existing candidate ID or `-1`;
- preference for the most complete existing name;
- explicit uncertainty behavior.

This is an **adapted approximation**. It cannot be labelled exact until the
policy has a ranked/top-1 entity-resolution path or an explicitly accepted
fairness exception.

### Fact extraction

Native Graphiti uses
[`extract_edges.edge`](../../graphiti_core/prompts/extract_edges.py). Agent-memory
preserves current episode, previous context, resolved entities, reference time,
distinct endpoints, relation type, fact text, and nullable temporal bounds.
Entity names are deliberately replaced by episode-local integer ordinals so
the view query can deterministically attach canonical entity IDs.

The policy currently omits or weakens:

- Graphiti's full detail-preservation rules for names, quantities, brands,
  colors, materials, locations, and activities;
- the rule distinguishing a more-specific new fact from a duplicate;
- the exact `SCREAMING_SNAKE_CASE` relation-type contract;
- Graphiti's ongoing-fact `valid_at` behavior and complete datetime rules;
- the provider-boundary language and Unicode instructions.

Ordinal endpoints are an intentional relational adaptation. The remaining
items are **missing parity** in the extraction instruction.

### Fact duplicate and contradiction resolution

Native Graphiti uses
[`dedupe_edges.resolve_edge`](../../graphiti_core/prompts/dedupe_edges.py) once
per new fact. One structured response returns both `duplicate_facts` and
`contradicted_facts`, with continuous indices across existing duplicate
candidates and broader invalidation candidates.

Agent-memory splits this into two declarative paths:

- duplicate claims are grouped by `_FACT_GROUP_INSTRUCTION`;
- canonical facts are self-compared by `_CONTRADICTORY_FACT_INSTRUCTION`, then
  temporal invalidation is constructed deterministically.

The split preserves the high-level duplicate-versus-contradiction distinction,
but it is not the same decision contract. In particular, it does not reproduce
Graphiti's candidate list boundaries, multi-ID action output, or the ability for
one existing fact to appear in both action lists. This is an **adapted
approximation** and a benchmark-relevant difference.

## Prompt paths not exercised by this smoke

These paths are classified explicitly rather than being silently treated as
covered:

| Prompt family | Status | Reason |
| --- | --- | --- |
| Entity summary batch prompts | missing comparison coverage | This run used Graphiti's direct short-summary/fact append path, so no summary LLM call appeared. Agent-memory still uses `_ENTITY_SUMMARY_INSTRUCTION`. |
| Entity/fact attribute extraction | intentionally excluded | No custom entity or edge attribute schema is enabled in the baseline. |
| Separate timestamp extraction | intentionally excluded | The standard edge extractor already emitted `valid_at` and `invalid_at`; no fallback timestamp call occurred. |
| Combined node-and-edge extraction | intentionally excluded | The pinned native `add_episode` path used separate extraction calls. |
| Community summary prompts | intentionally excluded | `update_communities=False` is part of the Phase 1 contract. |
| Saga summary prompt | intentionally excluded | The LOCOMO ingestion harness does not create or summarize sagas. |

## Retrieval boundary

The trace contained nine calls before retrieval and nine after both retrieval
passes. Native node BM25/cosine/RRF and fact BM25/cosine/BFS/cross-encoder
retrieval therefore made no generative LLM call. This is the retrieval boundary
the agent-memory comparison must preserve unless a separately named semantic
reranking experiment is introduced.

## Required parity work before the full benchmark

1. Expand entity extraction with Graphiti's current-message exclusions,
   specificity rules, and speaker handling without copying LOTUS's system
   responsibilities into the instruction.
2. Expand fact extraction with Graphiti's specificity, relation-type, and
   datetime rules while retaining ordinal endpoint output.
3. Decide and document whether ranked top-1 entity resolution is implemented or
   declared as a measured approximation.
4. Decide and document whether structured edge resolution is reproduced or the
   split duplicate/contradiction policy is retained as a measured approximation.
5. Trigger the native summary path with a dedicated fixture and audit it against
   agent-memory's entity aggregation prompt.

Until these five items are closed, benchmark artifacts must describe the
agent-memory policy as Graphiti-style rather than prompt-equivalent Graphiti.
