
# Data Flow Diagrams for Graphiti + Neo4j

## Add Episode Flow

This diagram shows the complete flow of `graphiti.add_episode()` operation, including all LLM calls, Neo4j queries, and data transformations.

**Typical Duration:** ~6 seconds (LLM accounts for 89% of time)

### Sequence Diagram

```mermaid
sequenceDiagram
    participant App as Graphiti Server
    participant Core as Graphiti Core
    participant LLM as LLM (local/api)
    participant Neo4j as Neo4j

    Note over App, Neo4j: === ADD EPISODE: "Alice works at TechCorp as a software engineer" ===

    App->>Core: add_episode(content, group_id, timestamp) | Aync Task

    Note over Core, Neo4j: Phase 1: Historical Context Query & Raw Episode Creation
    Core->>Neo4j: MATCH (e:Episodic) WHERE e.group_id=$gid<br/>RETURN e ORDER BY e.created_at DESC LIMIT 10
    Neo4j-->>Core: Previous episodes (for context)
    Core->>Neo4j: MERGE (e:Episodic {uuid: $uuid})<br/>SET e.content=$content, e.timestamp=$ts

    Note over Core, LLM: Phase 2: Entity Extraction
    Core->>LLM: extract_nodes.extract_message<br/>Prompt: "Extract entities from: Alice works at TechCorp..."<br/>+ previous_episodes + entity_types
    LLM-->>Core: JSON: [{name:"Alice", type:"person"}, {name:"TechCorp", type:"organization"}]

    Note over Core, Neo4j: Phase 3: Entity Deduplication
    loop For each extracted entity (Parallel for extracted nodes)
        Core->>Neo4j: Hybrid Search:<br/> BM25 Search: CALL db.index.fulltext.queryNodes('name_index', 'Alice')<br/>+ Cosine Search: vector.similarity.cosine(embedding)
        Neo4j-->>Core: Candidate nodes [{name:"Alice Chen", uuid:xxx}, ...]
        
        alt Candidates found
            Core->>LLM: dedupe_nodes.nodes<br/>Prompt: "Is 'Alice' the same as 'Alice Chen'?"<br/>+ node summaries
            LLM-->>Core: JSON: {is_duplicate: true, uuid: "xxx"} or {is_duplicate: false}
        end
    end

    Note over Core, LLM: Phase 4: Relationship Extraction
    Core->>LLM: extract_edges.edge<br/>Prompt: "Extract relationships between Alice and TechCorp"<br/>+ entity list + previous_episodes
    LLM-->>Core: JSON: [{source:"Alice", target:"TechCorp", relation:"WORKS_AT", fact:"Alice works at TechCorp as a software engineer"}]

    Note over Core, Neo4j: Phase 5: Edge Deduplication
    loop For each extracted edge (Parallel for extracted edges)
        Core->>Neo4j: BM25 Search: CALL db.index.fulltext.queryRelationships('fact_index', 'works at')<br/>+ Cosine Search on edge embeddings
        Neo4j-->>Core: Candidate edges [{fact:"Alice is employed at TechCorp", uuid:yyy}, ...]
        
        alt Candidates found
            Core->>LLM: dedupe_edges.resolve_edge<br/>Prompt: "Is NEW FACT duplicate of EXISTING FACTS?<br/>Does NEW FACT contradict INVALIDATION CANDIDATES?"
            LLM-->>Core: JSON: {duplicate_facts: [idx], contradicted_facts: [idx], fact_type: "..."}
        end
    end

    Note over Core, LLM: Phase 6: Entity Summary Generation (PARALLEL)
    par Generate summaries concurrently
        Core->>LLM: extract_nodes.extract_summary<br/>Prompt: "Summarize what we know about Alice"<br/>+ all edges involving Alice
        LLM-->>Core: "Alice is a software engineer working at TechCorp"
    and
        Core->>LLM: extract_nodes.extract_summary<br/>Prompt: "Summarize what we know about TechCorp"<br/>+ all edges involving TechCorp
        LLM-->>Core: "TechCorp is a technology company where Alice works"
    end

    Note over Core, Neo4j: Phase 7: Persist to Neo4j
    Core->>Neo4j: MERGE (n:Entity {uuid: $uuid})<br/>SET n.name=$name, n.summary=$summary, n.embedding=$vec
    Core->>Neo4j: MATCH (a:Entity {uuid:$src}), (b:Entity {uuid:$tgt})<br/>CREATE (a)-[r:RELATES_TO {fact:$fact, embedding:$vec}]->(b)
    Neo4j-->>Core: Committed

    Core-->>App: EpisodeResult (nodes, edges created)
```

### Time Breakdown (from trace logs)

| Phase | Duration | Component |
|-------|----------|-----------|
| Entity Extraction | ~1.7s | LLM |
| Entity Deduplication | ~1.3s | LLM |
| Edge Extraction | ~2.2s | LLM (slowest) |
| Summary Generation | ~0.6s | LLM (parallel) |
| Neo4j Queries | ~0.7s | DB |
| **Total** | **~6.5s** | **LLM: 89%** |

## Search Flow

This diagram shows the complete flow of `graphiti.search()` operation, including different search configurations and optional BFS traversal.

**Typical Duration:** ~130ms (no LLM reranking) or ~1-2s (with LLM reranking)

### Sequence Diagram

```mermaid
sequenceDiagram
    participant App as Graphiti Server
    participant Core as Graphiti Core
    participant Neo4j as Neo4j
    participant Reranker as Reranker (Optional)<br/>Cross-Encoder

    Note over App, Neo4j: === SEARCH: "What does Alice do?" ===

    App->>Core: search(query, group_ids, config)

    Note over Core, Neo4j: Phase 1: Generate Query Embedding
    Core->>Core: embedder.embed("What does Alice do?")
    Note right of Core: Local Model/API

    Note over Core, Neo4j: Phase 2: Parallel Retrieval Methods

    alt Config: EDGE_HYBRID_SEARCH_RRF (Default - No BFS)
        par Method 1: BM25 Full-text Search
            Core->>Neo4j: CALL db.index.fulltext.queryRelationships('edge_fact_fulltext', 'Alice do work')<br/>YIELD relationship, score<br/>RETURN relationship LIMIT 10
            Neo4j-->>Core: Edges by text relevance
        and Method 2: Cosine Similarity Search
            Core->>Neo4j: MATCH (a)-[r:RELATES_TO]->(b) WHERE r.group_id IN $group_ids<br/>WITH r, vector.similarity.cosine(r.embedding, $query_vec) AS score<br/>ORDER BY score DESC LIMIT 10
            Neo4j-->>Core: Edges by vector similarity
        end
        
        Note over Core: RRF Fusion: score = Σ 1/(k + rank_i)
        Core->>Core: Merge results using Reciprocal Rank Fusion (k=60)

    else Config: COMBINED_HYBRID_SEARCH_CROSS_ENCODER (With BFS)
        par Method 1: BM25 Search
            Core->>Neo4j: BM25 fulltext query
            Neo4j-->>Core: Text-matched edges
        and Method 2: Cosine Search
            Core->>Neo4j: Vector similarity query
            Neo4j-->>Core: Embedding-matched edges
        and Method 3: BFS from Center Node
            Note over Core, Neo4j: BFS requires center_node_uuid
            Core->>Neo4j: MATCH (start:Entity {uuid: $center_uuid})<br/>CALL apoc.path.expandConfig(start, {<br/>  maxLevel: $bfs_max_depth,<br/>  relationshipFilter: "RELATES_TO"<br/>})<br/>YIELD path<br/>RETURN relationships(path)
            Neo4j-->>Core: Edges within N hops of center node
        end

        Note over Core, Reranker: Cross-Encoder Reranking
        Core->>Reranker: cross_encoder.rank(query, facts)<br/>Options: OpenAIReranker (LLM logprobs) / BGEReranker (local model)
        Reranker-->>Core: Reranked edge list with scores
    end

    Note over Core, Neo4j: Phase 3: Fetch Full Edge Data
    Core->>Neo4j: MATCH (a)-[r:RELATES_TO]->(b)<br/>WHERE r.uuid IN $edge_uuids<br/>RETURN a.name, r.fact, b.name, r.created_at
    Neo4j-->>Core: Complete edge records

    Core-->>App: SearchResults [{fact, source, target, score, created_at}, ...]
```

### Search Configuration Comparison

| Search Config | BM25 | Cosine | BFS | LLM Rerank | Use Case |
|---------------|------|--------|-----|------------|----------|
| `EDGE_HYBRID_SEARCH_RRF` | ✅ | ✅ | ❌ | ❌ (RRF) | Fast retrieval, no LLM needed |
| `EDGE_HYBRID_SEARCH_NODE_DISTANCE` | ✅ | ✅ | ✅ | ❌ (RRF) | Explore from known node |
| `EDGE_HYBRID_SEARCH_CROSS_ENCODER` | ✅ | ✅ | ❌ | ✅ | High precision, needs LLM |
| `COMBINED_HYBRID_SEARCH_CROSS_ENCODER` | ✅ | ✅ | ✅ | ✅ | Most comprehensive, slowest |

### Time Breakdown

**Without LLM Reranking (~130ms):**

| Phase | Duration | Component |
|-------|----------|-----------|
| Embedding Generation | ~3ms | Local model |
| BM25 + Cosine Search | ~127ms | Neo4j |
| **Total** | **~130ms** | **100% DB** |

**With LLM Reranking (~1-2s):**

| Phase | Duration | Component |
|-------|----------|-----------|
| Embedding Generation | ~3ms | Local model |
| BM25 + Cosine + BFS | ~150ms | Neo4j |
| LLM Reranking | ~1-2s | LLM |
| **Total** | **~1.5s** | **LLM: 90%** |

