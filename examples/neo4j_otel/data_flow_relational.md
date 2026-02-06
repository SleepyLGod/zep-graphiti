# Graphiti Data Flow - Relational Algebra Notation

This document describes the data flow of Graphiti operations using relational algebra notation.

## Symbol Reference

### Relational Algebra Operators

| Symbol | Name | Meaning | SQL Equivalent |
|--------|------|---------|----------------|
| `σ` | Selection | Filter rows | `WHERE` |
| `π` | Projection | Select columns | `SELECT cols` |
| `τ↓` / `τ↑` | Sort | Order desc/asc | `ORDER BY ... DESC/ASC` |
| `⊗_{f}` | Cross product with score | Cartesian product with computed score | `CROSS JOIN` + computed column |
| `⋈` | Join | Natural/equi join | `JOIN` |
| `∪` | Union | Combine results | `UNION` |
| `∩` | Intersection | Common results | `INTERSECT` |
| `−` | Difference | Set difference | `EXCEPT` |

### Search & Aggregation Functions

| Symbol | Name | Meaning |
|--------|------|---------|
| `FTS(idx, q, G)` | Full-text search | BM25 search on index `idx` with query `q`, filtered by group_ids `G` |
| `cosine(v1, v2)` | Cosine similarity | Vector similarity between embeddings |
| `BFS(origins, d, G)` | Breadth-first search | Graph traversal from `origins` up to depth `d` |
| `RRF([...], θ)` | Reciprocal Rank Fusion | Rank fusion algorithm with min_score `θ` |
| `MMR(q_vec, vecs, λ, θ)` | Maximal Marginal Relevance | Diversity-aware reranking |
| `mode(X)` | Mode | Most frequent value in set `X` |
| `embed(text)` | Embedding | Convert text to vector (384-dim) |
| `[LIMIT k]` | Top-k | Return at most `k` results |

### LOTUS Semantic Operators

| Operator | Signature | Semantics | Langex Pattern |
|----------|-----------|-----------|----------------|
| `sem_map(l: X→Y)` | single row → new column | Projection generating new structured data | "Extract/Generate ... from input" |
| `sem_join(t: T, l: (X,Y)→Bool)` | two tables + predicate → matched rows | Semantic join by NL predicate | "Is X same as Y?" |
| `sem_filter(l: X→Bool)` | single row → bool | Filter rows by NL predicate | "Does X satisfy ...?" |
| `sem_agg(l: L[X]→X)` | multiple rows → single value | Aggregation reducing inputs | "Synthesize/Combine ..." |
| `sem_topk(k, l: L[X]→L[X])` | multiple rows → ranked top-k | Semantic ranking | "Rank by relevance to ..." |

### Logical Operators

| Symbol | Name | Meaning |
|--------|------|---------|
| `∀` | For all | Universal quantifier (iteration) |
| `∃` | Exists | Existential quantifier |
| `∧` | And | Logical conjunction |
| `∨` | Or | Logical disjunction |
| `¬` | Not | Logical negation |

### Variable Naming Convention

| Variable | Type | Description |
|----------|------|-------------|
| `m` | `text` | Raw message input |
| `g` | `str` | group_id |
| `t` | `datetime` | reference_time |
| `q` | `text` | Search query |
| `q_vec` | `list[float]` | Query embedding vector |
| `e` | `EpisodicNode` | Episode node |
| `n` | `EntityNode` | Entity node |
| `f` | `EntityEdge` | Entity edge (fact/relationship) |
| `c` | `CommunityNode` | Community node |
| `E_*` | `list[EpisodicNode]` | List of episodes |
| `N_*` | `list[EntityNode]` | List of entities |
| `F_*` | `list[EntityEdge]` | List of edges |
| `C_*` | `list[CommunityNode]` | List of communities |

---

## 1. add_episode(m, g, t) - Insert Flow

### Input
- `m : text` - Raw message content
- `g : str` - Group ID
- `t : datetime` - Reference time (valid_at)

### Output
- `AddEpisodeResults(episode, nodes, edges, communities)`



### Flow Diagram

```mermaid
sequenceDiagram
    participant Py as Python Server
    participant LLM as LLM
    participant Neo4j as Neo4j

    Note over Py, Neo4j: Input: m : text, g : str, t : datetime

    rect rgb(245, 245, 220)
        Note over Py, Neo4j: Phase 1: Context Retrieval
        Py->>Neo4j: E_prev = τ_{created_at↓} (σ_{group_id=g} (Episodic)) [LIMIT 10]
        Neo4j-->>Py: E_prev : list[EpisodicNode]
        Note right of Py: e = EpisodicNode(content=m, group_id=g, valid_at=t)<br/>(in-memory, uuid generated)
    end

    rect rgb(230, 240, 255)
        Note over Py, LLM: Phase 2: Entity Extraction
        Note right of Py: sem_map((m, E_prev), "Extract entity nodes<br/>mentioned in CURRENT MESSAGE")
        Py->>LLM: extract_nodes(m, E_prev)
        LLM-->>Py: N_ext : list[{name, type_id}]
        Note right of Py: Convert to EntityNode objects<br/>N_raw : list[EntityNode]
    end

    rect rgb(245, 245, 220)
        Note over Py, Neo4j: Phase 3: Entity Deduplication
        par ∀ n ∈ N_raw (parallel hybrid search)
            Py->>Neo4j: C_n = RRF(<br/>  FTS('node_name_and_summary', n.name, g) [LIMIT 2k],<br/>  τ_{score↓}(σ_{score>0.6}(Entity ⊗_{cosine(name_embedding, embed(n.name))})) [LIMIT 2k]<br/>)
            Neo4j-->>Py: C_n : list[EntityNode]
        end
        Note right of Py: C_all = dedupe(⋃ C_n)
        alt |C_all| > 0 ∧ ∃ unresolved
            Note right of Py: sem_join(N_raw × C_all, "Is new entity<br/>same real-world object as existing?")
            Py->>LLM: dedupe_nodes(N_raw, C_all, m, E_prev) → single LLM call
            LLM-->>Py: R : list[{id, name, duplicate_idx}]
        end
        Note right of Py: Build uuid_map: new_uuid → existing_uuid<br/>N : list[EntityNode] (resolved)
    end

    rect rgb(230, 240, 255)
        Note over Py, LLM: Phase 4: Edge Extraction
        Note right of Py: sem_map((m, N, E_prev, t), "Extract fact triples<br/>between entities from message")
        Py->>LLM: extract_edges(m, N_raw, E_prev)
        LLM-->>Py: F_ext : list[{src_id, tgt_id, relation, fact, valid_at}]
        Note right of Py: Convert to EntityEdge, remap UUIDs<br/>F_raw : list[EntityEdge]
    end

    rect rgb(245, 245, 220)
        Note over Py, Neo4j: Phase 5: Edge Deduplication
        Note right of Py: ∀ f ∈ F_raw: f.fact_embedding = embed(f.fact)
        par ∀ f ∈ F_raw (get edges between same nodes)
            Py->>Neo4j: E_between = σ_{src=f.src ∧ tgt=f.tgt} (RELATES_TO)
            Neo4j-->>Py: E_between : list[EntityEdge]
        end
        par ∀ f ∈ F_raw (hybrid search for related, filtered by E_between)
            Py->>Neo4j: C_f = RRF(<br/>  FTS('edge_name_and_fact', f.fact, g) [LIMIT k],<br/>  τ_{score↓}(σ_{score>0.6 ∧ uuid∈E_between}(RELATES_TO ⊗_{cosine(fact_embedding, f.embedding)})) [LIMIT k]<br/>)
            Neo4j-->>Py: C_f : list[EntityEdge] (related)
        end
        par ∀ f ∈ F_raw (hybrid search for invalidation, no filter)
            Py->>Neo4j: I_f = RRF(<br/>  FTS('edge_name_and_fact', f.fact, g) [LIMIT k],<br/>  τ_{score↓}(σ_{score>0.6}(RELATES_TO ⊗_{cosine(fact_embedding, f.embedding)})) [LIMIT k]<br/>)
            Neo4j-->>Py: I_f : list[EntityEdge] (invalidation candidates)
        end
        par ∀ f : |C_f| > 0 (parallel LLM calls per edge)
            Note right of Py: sem_join({f} × C_f, "Is new fact identical?")<br/>sem_filter(I_f, "Does new fact contradict?")
            Py->>LLM: dedupe_edge(f.fact, C_f, I_f)
            LLM-->>Py: {duplicate_facts[], contradicted_facts[]}
        end
        Note right of Py: F : list[EntityEdge] (new/updated)<br/>F_inv : list[EntityEdge] (invalidated)
    end

    rect rgb(230, 240, 255)
        Note over Py, LLM: Phase 6: Summary Generation
        par ∀ n ∈ N (parallel)
            Note right of Py: sem_map((n, m, E_prev), "Generate summary<br/>for entity from messages")
            Py->>LLM: extract_summary(n, m, E_prev)
            LLM-->>Py: n.summary : str
        end
        Note right of Py: ∀ n ∈ N: n.name_embedding = embed(n.name)
    end

    rect rgb(255, 240, 230)
        Note over Py, Neo4j: Phase 7: Persist to DB
        Py->>Neo4j: MERGE (e:Episodic {uuid}) SET {content, group_id, valid_at, ...}
        Py->>Neo4j: ∀ n ∈ N: MERGE (n:Entity {uuid}) SET {name, summary, ...}<br/>+ setNodeVectorProperty('name_embedding', vec)
        Py->>Neo4j: ∀ n ∈ N: MERGE (e:Episodic)-[:MENTIONS]->(n:Entity)
        Py->>Neo4j: ∀ f ∈ (F ∪ F_inv): MERGE (src)-[r:RELATES_TO {uuid}]->(tgt)<br/>SET {fact, valid_at, invalid_at, ...}<br/>+ setRelationshipVectorProperty('fact_embedding', vec)
        Neo4j-->>Py: committed
    end

    rect rgb(240, 255, 240)
        Note over Py, Neo4j: Phase 8: Community Update (optional)
        opt update_communities = True
            par ∀ n ∈ N (parallel)
                Py->>Neo4j: c_exist = σ_{(c)-[:HAS_MEMBER]->(n)} (Community)
                Neo4j-->>Py: c_exist : CommunityNode | null
                alt c_exist = null
                    Py->>Neo4j: C_neighbor = π_Community (σ_{(c)-[:HAS_MEMBER]->(m)-[:RELATES_TO]-(n)} (Community))
                    Neo4j-->>Py: C_neighbor : list[CommunityNode]
                    Note right of Py: c = mode(C_neighbor) — most frequent
                end
                alt c ≠ null
                    Note right of Py: sem_agg([n.summary, c.summary],<br/>"Synthesize into single summary")
                    Py->>LLM: summarize_pair(n.summary, c.summary)
                    LLM-->>Py: c.summary_new
                    Note right of Py: sem_map(c.summary_new,<br/>"Create description of summary")
                    Py->>LLM: summary_description(c.summary_new)
                    LLM-->>Py: c.name_new
                    Note right of Py: c.name_embedding = embed(c.name_new)
                    Py->>Neo4j: MERGE (c:Community {uuid}) SET {name, summary, ...}<br/>+ setNodeVectorProperty('name_embedding', vec)
                    opt is_new = True (from neighbor)
                        Py->>Neo4j: MERGE (c)-[:HAS_MEMBER]->(n)
                    end
                end
            end
        end
    end

    Py-->>Py: return AddEpisodeResults(episode=e, nodes=N, edges=F∪F_inv, communities=...)
```

### Query Verification

| Phase | Query | Source Code Reference | Verified |
|-------|-------|----------------------|----------|
| 1 | `τ_{created_at↓} (σ_{group_id=g} (Episodic)) [LIMIT 10]` | `graphiti.py:870-880` | ✅ |
| 3 | `FTS('node_name_and_summary', n.name, g)` | `search_utils.py:641-654` | ✅ |
| 3 | `Entity ⊗_{cosine(name_embedding, vec)}` | `search_utils.py:754-770` | ✅ |
| 5 | `σ_{src=f.src ∧ tgt=f.tgt} (RELATES_TO)` | `edge_operations.py:321-326` | ✅ |
| 5 | `FTS('edge_name_and_fact', f.fact, g)` | `search_utils.py:269-282` | ✅ |
| 5 | `RELATES_TO ⊗_{cosine(fact_embedding, vec)}` | `search_utils.py:414-428` | ✅ |
| 6 | Summary uses only `(n, m, E_prev)` | `node_operations.py:644-653` | ✅ |
| 8 | `σ_{(c)-[:HAS_MEMBER]->(n)} (Community)` | `community_operations.py:272-279` | ✅ |
| 8 | `mode(C_neighbor)` not `LIMIT 1` | `community_operations.py:305-321` | ✅ |


---

## 2. build_communities(group_ids) - Community Creation Flow

### Input
- `G : list[str] | null` - Group IDs to process (null = all groups)

### Output
- `(communities : list[CommunityNode], edges : list[CommunityEdge])`

### Flow Diagram

```mermaid
sequenceDiagram
    participant Py as Python Server
    participant LLM as LLM
    participant Neo4j as Neo4j

    Note over Py, Neo4j: Input: G : list[str] | null

    rect rgb(255, 230, 230)
        Note over Py, Neo4j: Phase 1: Clear Existing Communities
        Py->>Neo4j: MATCH (c:Community) DETACH DELETE c
        Neo4j-->>Py: deleted
    end

    rect rgb(245, 245, 220)
        Note over Py, Neo4j: Phase 2: Get Group IDs (if null)
        alt G = null
            Py->>Neo4j: G = π_{DISTINCT group_id} (σ_{group_id IS NOT NULL} (Entity))
            Neo4j-->>Py: G : list[str]
        end
    end

    rect rgb(230, 240, 255)
        Note over Py, Neo4j: Phase 3: Build Graph Projection (per group)
        loop ∀ g ∈ G
            Py->>Neo4j: N_g = σ_{group_id=g} (Entity)
            Neo4j-->>Py: N_g : list[EntityNode]
            loop ∀ n ∈ N_g
                Py->>Neo4j: neighbors(n) = π_{m.uuid, count(e)} (<br/>  (n:Entity {uuid})-[e:RELATES_TO]-(m:Entity {group_id=g})<br/>)
                Neo4j-->>Py: list[{uuid, edge_count}]
            end
            Note right of Py: projection[g] = {n.uuid → [{neighbor_uuid, edge_count}, ...]}
        end
    end

    rect rgb(240, 255, 240)
        Note over Py: Phase 4: Label Propagation Clustering (in-memory)
        Note right of Py: Algorithm:<br/>1. ∀ n: label[n] = n.uuid (initial)<br/>2. repeat:<br/>   ∀ n: label[n] = mode(label[neighbor], weighted by edge_count)<br/>   until no change<br/>3. clusters = group_by(label)
        Note right of Py: Output: clusters : list[list[str]] (uuid lists)
    end

    rect rgb(230, 240, 255)
        Note over Py, LLM: Phase 5: Build Community Summaries (parallel per cluster)
        par ∀ cluster ∈ clusters
            Py->>Neo4j: entities = σ_{uuid ∈ cluster} (Entity)
            Neo4j-->>Py: entities : list[EntityNode]
            Note right of Py: summaries = [e.summary for e in entities]
            loop while |summaries| > 1 (pairwise reduction)
                Note right of Py: sem_agg(partition=pairwise)
                par ∀ (s1, s2) ∈ pairs(summaries)
                    Note right of Py: sem_agg([s1, s2], "Synthesize into one")
                    Py->>LLM: summarize_pair(s1, s2)
                    LLM-->>Py: merged_summary
                end
                Note right of Py: summaries = merged + odd_one_out
            end
            Note right of Py: community_summary = summaries[0]
            Note right of Py: sem_map(summary, "Create description")
            Py->>LLM: summary_description(community_summary)
            LLM-->>Py: community_name
            Note right of Py: c = CommunityNode(name, summary, group_id)<br/>c.name_embedding = embed(c.name)
        end
    end

    rect rgb(255, 240, 230)
        Note over Py, Neo4j: Phase 6: Persist Communities
        par ∀ c ∈ communities
            Py->>Neo4j: MERGE (c:Community {uuid}) SET {name, summary, group_id, ...}<br/>+ setNodeVectorProperty('name_embedding', vec)
        end
        par ∀ (c, n) ∈ community_edges
            Py->>Neo4j: MERGE (c:Community)-[:HAS_MEMBER]->(n:Entity)
        end
        Neo4j-->>Py: committed
    end

    Py-->>Py: return (communities, edges)
```

### Query Verification

| Phase | Query | Source Code Reference | Verified |
|-------|-------|----------------------|----------|
| 1 | `MATCH (c:Community) DETACH DELETE c` | `community_operations.py:remove_communities()` | ✅ |
| 2 | `π_{DISTINCT group_id} (Entity)` | `community_operations.py:42-52` | ✅ |
| 3 | `(n)-[e:RELATES_TO]-(m)` with count | `community_operations.py:58-79` | ✅ |
| 4 | Label Propagation (in-memory) | `community_operations.py:94-139` | ✅ |
| 5 | Pairwise summary merging | `community_operations.py:175-198` | ✅ |
| 5 | `summary_description()` for name | `community_operations.py:159-172` | ✅ |


---

## 3. search(q, G, cfg) - Search Flow

### Input
- `q : text` - Search query
- `G : list[str] | null` - Group IDs to search (null = all groups)
- `cfg : SearchConfig` - Configuration for search methods and rerankers

### Output
- `SearchResults(edges, nodes, episodes, communities, scores)`

### Flow Diagram

```mermaid
sequenceDiagram
    participant Py as Python Server
    participant LLM as LLM/CrossEncoder
    participant Neo4j as Neo4j

    Note over Py, Neo4j: Input: q : text, G : list[str] | null, cfg : SearchConfig

    rect rgb(245, 245, 220)
        Note over Py: Phase 1: Query Embedding
        alt cfg requires cosine similarity or MMR
            Note right of Py: q_vec = embed(q) : list[float] (384-dim)
        else
            Note right of Py: q_vec = [0.0] * 384 (placeholder)
        end
    end

    rect rgb(230, 240, 255)
        Note over Py, Neo4j: Phase 2: Parallel Search (4 concurrent searches)
        par edge_search (if cfg.edge_config ≠ null)
            alt BM25 ∈ cfg.edge_config.search_methods
                Py->>Neo4j: E_bm25 = FTS('edge_name_and_fact', q, G) [LIMIT 2k]
                Neo4j-->>Py: E_bm25 : list[EntityEdge]
            end
            alt cosine ∈ cfg.edge_config.search_methods
                Py->>Neo4j: E_cos = τ_{score↓}(σ_{score>θ ∧ group_id∈G}(<br/>  RELATES_TO ⊗_{cosine(fact_embedding, q_vec)}<br/>)) [LIMIT 2k]
                Neo4j-->>Py: E_cos : list[EntityEdge]
            end
            alt BFS ∈ cfg.edge_config.search_methods
                Py->>Neo4j: E_bfs = (origin)-[:RELATES_TO|MENTIONS*1..d]->(Entity)-[e:RELATES_TO]->() [LIMIT 2k]
                Neo4j-->>Py: E_bfs : list[EntityEdge]
            end
            Note right of Py: E_all = dedupe(E_bm25 ∪ E_cos ∪ E_bfs)
        and node_search (if cfg.node_config ≠ null)
            alt BM25 ∈ cfg.node_config.search_methods
                Py->>Neo4j: N_bm25 = FTS('node_name_and_summary', q, G) [LIMIT 2k]
                Neo4j-->>Py: N_bm25 : list[EntityNode]
            end
            alt cosine ∈ cfg.node_config.search_methods
                Py->>Neo4j: N_cos = τ_{score↓}(σ_{score>θ ∧ group_id∈G}(<br/>  Entity ⊗_{cosine(name_embedding, q_vec)}<br/>)) [LIMIT 2k]
                Neo4j-->>Py: N_cos : list[EntityNode]
            end
            alt BFS ∈ cfg.node_config.search_methods
                Py->>Neo4j: N_bfs = (origin)-[:RELATES_TO|MENTIONS*1..d]->(n:Entity) [LIMIT 2k]
                Neo4j-->>Py: N_bfs : list[EntityNode]
            end
            Note right of Py: N_all = dedupe(N_bm25 ∪ N_cos ∪ N_bfs)
        and episode_search (if cfg.episode_config ≠ null)
            Py->>Neo4j: Ep_bm25 = FTS('episode_content', q, G) [LIMIT 2k]
            Neo4j-->>Py: Ep_bm25 : list[EpisodicNode]
            Note right of Py: Ep_all = Ep_bm25 (BM25 only, no cosine)
        and community_search (if cfg.community_config ≠ null)
            Py->>Neo4j: C_bm25 = FTS('community_name', q, G) [LIMIT 2k]
            Neo4j-->>Py: C_bm25 : list[CommunityNode]
            Py->>Neo4j: C_cos = τ_{score↓}(σ_{score>θ ∧ group_id∈G}(<br/>  Community ⊗_{cosine(name_embedding, q_vec)}<br/>)) [LIMIT 2k]
            Neo4j-->>Py: C_cos : list[CommunityNode]
            Note right of Py: C_all = dedupe(C_bm25 ∪ C_cos)
        end
    end

    rect rgb(240, 255, 240)
        Note over Py, LLM: Phase 3: Reranking (per result type)
        par Edge Reranking
            alt cfg.edge_config.reranker = RRF
                Note right of Py: E_ranked, scores = RRF([E_bm25, E_cos, E_bfs], θ)
            else cfg.edge_config.reranker = MMR
                Py->>Neo4j: vecs = π_{uuid, fact_embedding} (σ_{uuid∈E_all} (RELATES_TO))
                Neo4j-->>Py: vecs : dict[uuid, embedding]
                Note right of Py: E_ranked, scores = MMR(q_vec, vecs, λ, θ)
            else cfg.edge_config.reranker = cross_encoder
                Note right of Py: sem_topk(k, E_all, "Rank by relevance to q")
                Py->>LLM: rank(q, [e.fact for e in E_all[:k]])
                LLM-->>Py: E_ranked, scores
            else cfg.edge_config.reranker = node_distance
                Py->>Neo4j: neighbors = σ_{(center)-[:RELATES_TO]-(n)} (E_all.source_nodes)
                Neo4j-->>Py: neighbors (score=1) vs non-neighbors (score=∞)
                Note right of Py: E_ranked = τ_{score↑}(E_all)
            else cfg.edge_config.reranker = episode_mentions
                Note right of Py: E_ranked = τ_{|e.episodes|↓}(RRF(E_all))
            end
        and Node Reranking
            alt cfg.node_config.reranker = RRF
                Note right of Py: N_ranked, scores = RRF([N_bm25, N_cos, N_bfs], θ)
            else cfg.node_config.reranker = MMR
                Py->>Neo4j: vecs = π_{uuid, name_embedding} (σ_{uuid∈N_all} (Entity))
                Neo4j-->>Py: vecs : dict[uuid, embedding]
                Note right of Py: N_ranked, scores = MMR(q_vec, vecs, λ, θ)
            else cfg.node_config.reranker = cross_encoder
                Note right of Py: sem_topk(k, N_all, "Rank by relevance to q")
                Py->>LLM: rank(q, [n.name for n in N_all])
                LLM-->>Py: N_ranked, scores
            else cfg.node_config.reranker = episode_mentions
                Py->>Neo4j: counts = π_{n.uuid, count(*)} ((Episodic)-[:MENTIONS]->(n:Entity))
                Neo4j-->>Py: counts : dict[uuid, int]
                Note right of Py: N_ranked = τ_{count↓}(RRF(N_all))
            else cfg.node_config.reranker = node_distance
                Py->>Neo4j: neighbors = σ_{(center)-[:RELATES_TO]-(n)} (N_all)
                Neo4j-->>Py: neighbors (score=1) vs non-neighbors (score=∞)
                Note right of Py: N_ranked = τ_{score↑}(N_all)
            end
        and Episode Reranking
            alt cfg.episode_config.reranker = RRF
                Note right of Py: Ep_ranked, scores = RRF([Ep_bm25], θ)
            else cfg.episode_config.reranker = cross_encoder
                Note right of Py: Ep_rrf = RRF([Ep_bm25])[:k]
                Note right of Py: sem_topk(k, Ep_rrf, "Rank by relevance to q")
                Py->>LLM: rank(q, [ep.content for ep in Ep_rrf])
                LLM-->>Py: Ep_ranked, scores
            end
        and Community Reranking
            alt cfg.community_config.reranker = RRF
                Note right of Py: C_ranked, scores = RRF([C_bm25, C_cos], θ)
            else cfg.community_config.reranker = MMR
                Py->>Neo4j: vecs = π_{uuid, name_embedding} (σ_{uuid∈C_all} (Community))
                Neo4j-->>Py: vecs : dict[uuid, embedding]
                Note right of Py: C_ranked, scores = MMR(q_vec, vecs, λ, θ)
            else cfg.community_config.reranker = cross_encoder
                Note right of Py: sem_topk(k, C_all, "Rank by relevance to q")
                Py->>LLM: rank(q, [c.name for c in C_all])
                LLM-->>Py: C_ranked, scores
            end
        end
    end

    rect rgb(255, 240, 230)
        Note over Py: Phase 4: Apply Limit & Return
        Note right of Py: edges = E_ranked[:cfg.limit]<br/>nodes = N_ranked[:cfg.limit]<br/>episodes = Ep_ranked[:cfg.limit]<br/>communities = C_ranked[:cfg.limit]
    end

    Py-->>Py: return SearchResults(edges, nodes, episodes, communities, scores)
```

### Query Verification

| Phase | Query | Source Code Reference | Verified |
|-------|-------|----------------------|----------|
| 1 | Embedding only if cosine/MMR needed | `search.py:88-109` | ✅ |
| 2 | `FTS('edge_name_and_fact', q, G)` | `search_utils.py:182-294` | ✅ |
| 2 | `RELATES_TO ⊗_{cosine(fact_embedding, q_vec)}` | `search_utils.py:297-442` | ✅ |
| 2 | Edge BFS with depth | `search_utils.py:445-570` | ✅ |
| 2 | `FTS('node_name_and_summary', q, G)` | `search_utils.py:573-666` | ✅ |
| 2 | `Entity ⊗_{cosine(name_embedding, q_vec)}` | `search_utils.py:669-784` | ✅ |
| 2 | Node BFS with depth | `search_utils.py:787-880` | ✅ |
| 2 | `FTS('episode_content', q, G)` - BM25 only | `search_utils.py:883-966` | ✅ |
| 2 | `FTS('community_name', q, G)` | `search_utils.py:969-1055` | ✅ |
| 2 | `Community ⊗_{cosine(name_embedding, q_vec)}` | `search_utils.py:1058-1173` | ✅ |
| 3 | RRF algorithm | `search_utils.py:1777-1792` | ✅ |
| 3 | MMR algorithm | `search_utils.py:1898-1936` | ✅ |
| 3 | node_distance: direct neighbor check (depth=1) | `search_utils.py:1795-1854` | ✅ |
| 3 | episode_mentions: count (Episodic)-[:MENTIONS]->(Entity) | `search_utils.py:1857-1895` | ✅ |

### Important Notes

1. **Episode search has NO cosine similarity** - only BM25 full-text search is available
2. **node_distance_reranker checks direct neighbors only** (depth=1), not shortest path
3. **episode_mentions_reranker** counts how many episodes mention each entity
4. **RRF formula**: `score[uuid] = Σ 1/(rank + k)` where k=1 (rank_const)
5. **MMR formula**: `mmr = λ * cosine(q, doc) + (λ-1) * max_sim(doc, selected)`


