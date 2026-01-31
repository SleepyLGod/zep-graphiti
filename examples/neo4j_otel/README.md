# Graphiti + Neo4j + OpenTelemetry Demo

This notebook demonstrates how to use Graphiti with Neo4j and observe the complete call chain through OpenTelemetry tracing.

## Environment Setup

### 1. Install Dependencies with uv

This project uses `uv` for dependency management (not conda).

```bash
cd projects/zep-repos/zep-graphiti/examples/neo4j_otel

# Install dependencies (creates .venv automatically)
uv sync
```

### 2. Register Jupyter Kernel

To use the uv virtual environment in Jupyter notebooks:

```bash
# Register the kernel
.venv/bin/python -m ipykernel install --user --name=graphiti-neo4j-otel --display-name="Python (graphiti-neo4j-otel)"
```

Now you can select "Python (graphiti-neo4j-otel)" as the kernel in Jupyter.

### 3. Configure API Key

```bash
# Copy .env template
cp .env.example .env

# Edit .env file and fill in your API Key
```

**DeepSeek Users Note**:
- DeepSeek's LLM API is OpenAI-compatible and works normally
- DeepSeek does not provide an official embedding API, so you need to:
  - Use local embedding with sentence-transformers (default, no API key needed), or
  - Configure OpenAI key for embeddings

### 4. Start Neo4j

```bash
cd projects/zep-repos/zep-graphiti

# Start Neo4j container
docker compose up neo4j -d

# Check status
docker compose ps

# Wait for Neo4j to start (about 10-30 seconds)
# You can access http://localhost:7474 to view Neo4j Browser
```

### 5. Run Notebook

```bash
cd projects/zep-repos/zep-graphiti/examples/neo4j_otel

# Start JupyterLab using uv
uv run jupyter lab
```

Then open `graphiti_neo4j_otel_demo.ipynb` in your browser and select the **"Python (graphiti-neo4j-otel)"** kernel.

## What You Will See

After running the notebook, you will see in the output:

1. **OpenTelemetry Spans** - Tracing information for each operation
2. **Neo4j Cypher Queries** - Specific statements for creating nodes and edges
3. **LLM Calls** - Entity extraction prompts and responses
4. **Embedding Generation** - Text vectorization process

## Troubleshooting

### Neo4j Connection Failed
```bash
# Check if Neo4j is running
docker ps | grep neo4j

# View Neo4j logs
docker compose logs neo4j
```

### API Key Error
Make sure the key in `.env` file is correctly formatted without extra spaces.

### Port Conflict
If ports 7474 or 7687 are occupied:
```bash
# Check port usage
ss -tlnp | grep -E "(7474|7687)"

# Stop the occupying process or modify port mapping in docker-compose.yml
```

