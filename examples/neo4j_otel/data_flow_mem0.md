# Data Flow Diagrams for Mem0

## Memory Update

```mermaid
sequenceDiagram
    participant Client
    participant Server as Mem0 Server
    participant LLM
    participant VecDB as Vector DB
    participant GraphDB as Graph DB (Neo4j)

    Client->>Server: add(messages)
    
    par Basic Mem0 Flow
        Server->>LLM: Extraction Prompt (InputMsg) | 👍 sem_map
        LLM-->>Server: List[Facts]
        
        loop For each Fact
            Server->>VecDB: Search similar memories (Top-K) | 👍 topk
            VecDB-->>Server: Related Old Memories
        end
        
        Server->>LLM: Update Prompt (OldMemories + NewFacts) | 👍 sem_map
        LLM-->>Server: Operations [ADD, UPDATE, DELETE]
        
        Server->>VecDB: Execute CRUD Operations
    and Graph Mem0 Flow (if enabled)
        Server->>LLM: Entity Extraction Prompt (InputMsg) | 👍 sem_map
        LLM-->>Server: List[Entities] (Source, Dest)
        
        Server->>LLM: Relation Extraction Prompt (InputMsg + Entities) | 👍 sem_map
        LLM-->>Server: List[Relations] (Source, Rel, Dest)
        
        Server->>GraphDB: Search Nodes (Vector Sim) + Traverse 1-hop | 👍 topk
        GraphDB-->>Server: Existing Subgraphs
        
        Server->>LLM: Delete Decision Prompt (Existing + Input)
        LLM-->>Server: Delete Ops
        Server->>GraphDB: Execute DELETE
        
        Server->>GraphDB: Merge/Create Nodes & Relations (ADD)
    end
    
    Server-->>Client: Success
```

## Memory Search

```mermaid
sequenceDiagram
    participant Client
    participant Server as Mem0 Server
    participant LLM
    participant VecDB as Vector DB
    participant GraphDB as Graph DB (Neo4j)

    Client->>Server: search(query)

    par Basic Mem0 Retrieval
        Server->>VecDB: Embed(query) -> ANN Search | 👍 topk
        VecDB-->>Server: Top-K Facts
    and Graph Mem0 Retrieval (if enabled)
        Server->>LLM: Entity Extraction Prompt (query) | 👍 sem_map
        LLM-->>Server: List[QueryEntities]
        
        loop For each QueryEntity
            Server->>GraphDB: Vector Search (Node Embedding) | 👍 topk
            GraphDB-->>Server: Top Matching Nodes
            Server->>GraphDB: Traverse 1-hop Neighbors (Incoming/Outgoing)
            GraphDB-->>Server: Subgraph (Triplets)
        end
        
        Server->>Server: Rerank (BM25) on Triplets | 👍 topk
    end
    
    Server-->>Client: Aggregated Results
```
