# Architecture

KoshurLock Holmes is a self-hosted, open-source AI incident investigator. The entire memory stack runs on FalkorDB (`falkordb/falkordb:latest`) for high-performance Graph + Vector context and SQLite for lightweight relational metadata. There are no proprietary services and no OpenAI dependency.

## Components

- Frontend: React, Vite, TypeScript, Tailwind, and a real interactive knowledge graph rendered with Cytoscape.js. Served by nginx, which also reverse-proxies `/api` to the backend so the browser stays same-origin.
- Backend: Python 3.12, FastAPI, uvicorn with a single worker. It wraps Cognee 1.2.2 and the native `falkordb` client.
- Memory engine: Cognee 1.2.2 with `cognee-community-hybrid-adapter-falkor`. The language model is Groq through a LiteLLM custom provider. Embeddings are computed locally with FastEmbed (all-MiniLM-L6-v2, 384 dimensions).
- Storage: FalkorDB (`falkordb/falkordb:latest`) providing low-latency OpenCypher graph traversals and vector indexing on port 6379, paired with SQLite for relational metadata.

```mermaid
flowchart TB
  UI["React + Cytoscape.js dashboard (nginx :8080)"]
  API["FastAPI backend (uvicorn, single worker) :8000"]
  ENG["Cognee engine: remember, recall, improve, forget"]
  FC["FalkorDB Client: OpenCypher multi-hop & IoCs"]
  subgraph Storage["Storage Layer"]
    FALKOR["FalkorDB :6379 (OpenCypher Graph + Vector Index)"]
    SQLITE["SQLite (cognee_db relational metadata)"]
  end
  GROQ["Groq LLM via LiteLLM"]
  EMB["FastEmbed (local, 384-dim)"]

  UI -->|/api REST| API
  API --> ENG
  API --> FC
  ENG -->|Cognee Falkor Adapter| Storage
  FC <-->|OpenCypher Queries| FALKOR
  ENG <--> GROQ
  ENG <--> EMB
```

## Data flow

1. A SOC analyst has scattered enterprise logs (VPN, file access, email, badge, CCTV, HR, threat intel, tips), provided as files. For the demo these live in `backend/data`; for a new case they are uploaded.
2. Ingestion (remember): each source is converted to provenance-wrapped text and added, then cognified. Cognee extracts entities and relationships and writes the knowledge graph into Postgres.
3. Investigation (recall): the analyst asks plain-English questions. Cognee runs a graph-completion search that traverses the graph across all sources and returns a cited, multi-hop answer plus the connected entities and a timeline.
4. Correction (improve): the analyst teaches a confirmed finding, which is added and re-cognified so the graph reweights.
5. Retraction (forget): the analyst forgets a planted or discredited source, and any conclusion built on it collapses so the truth re-derives.
6. The frontend reads the graph as typed nodes and edges, the reconstructed timeline, source status, raw log lines for citations, and system health.

## REST API

All paths are served by FastAPI and reached from the browser under the `/api` prefix, which nginx strips.

- `GET /` API index listing the operations and views.
- `GET /health` liveness plus a direct Postgres connectivity check.
- `GET /status` provider summary (LLM, embeddings, storage), graph node and edge counts, and whether any OpenAI key is present.
- `GET /sources` the active case's evidence list with per-source ingest status.
- `GET /evidence/{filename}` the raw provenance-wrapped log lines for a source, used by citations.
- `GET /graph` the knowledge graph as SOC-typed nodes and edges, with optional structural scaffolding.
- `GET /timeline` the deterministic chronological events for the active case, filterable by actor.
- `POST /ask` recall: a cited, multi-hop answer with connected entities and a timeline.
- `POST /teach` improve: add a confirmed analyst correction and re-cognify.
- `POST /forget` forget: surgically remove one source so conclusions built on it collapse.
- `POST /ingest` remember: build the graph from the seeded evidence, short-circuiting when it is already warm.
- `GET /cases` the case registry with the active and materialized case ids.
- `POST /cases` create a new upload case.
- `POST /cases/{id}/files` upload evidence files to a case.
- `GET /cases/{id}/files` per-file ingest status for a case, used for live polling.
- `POST /cases/{id}/ingest` ingest an uploaded case's files through the real remember path.
- `POST /cases/{id}/open` activate a case and materialize it in the graph if needed.
- `DELETE /cases/{id}` delete an uploaded case and its files. The demo case is protected and cannot be deleted.

## Data model

Cognee stores domain entities as graph nodes linked by typed relationships. The graph transform classifies each node into a small SOC ontology for the visualization: Person, Account, Device, IP, File, Location, and Event, with Document for structural scaffolding and Other as a fallback. Nodes are colored and shaped by type, sized by connectivity, and marked when they match an indicator of compromise. Edges carry the relationship name. The timeline is built deterministically from the evidence text rather than the graph, so it is exact and needs no LLM call.

## Two-mode case model and graph-swap

The app supports a warm seeded demo and a real upload path, and it keeps exactly one case materialized in the graph at a time. This graph-swap model avoids blending answers across cases. The registry tracks which case is active (what the UI is viewing) and which is materialized (whose data is in the graph). Loading the demo restores it warm from the snapshot. Starting a new investigation creates a case, and ingesting it prunes the graph and builds the uploaded case in its own dataset. Deleting the materialized case prunes it and restores the demo, so the graph is never left empty or polluted.

## Why FalkorDB for Graph and Vector Context

FalkorDB (`falkordb/falkordb:latest`) provides high-performance graph traversal and vector indexing:

- Native OpenCypher support: enables expressive graph queries for multi-hop attack path correlation, degree centrality hub ranking, and alibi verification.
- Sub-millisecond surgical source retraction: retracting false clues (e.g. `anonymous_tip.txt`) is performed via OpenCypher `DETACH DELETE` without requiring complete graph rebuilds.
- Cognee FalkorDB Hybrid Adapter (`cognee-community-hybrid-adapter-falkor`): routes Cognee's graph and vector operations directly to FalkorDB.
- SQLite relational engine: lightweight, self-contained, file-based relational store for Cognee's dataset and data record tracking.

## Configuration that makes it work

- LLM: Groq via LiteLLM, `LLM_PROVIDER=custom` with a `groq/` model, and `LLM_INSTRUCTOR_MODE=json_mode`. Groq's Llama models mangle json_schema mode into a broken tool-call path that aborts cognify; plain json_mode is reliable.
- Embeddings: local FastEmbed, `EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2` with `EMBEDDING_DIMENSIONS=384`. The dimension must be set, otherwise Cognee defaults to 3072 and silently falls back to OpenAI.
- Storage: `VECTOR_DB_PROVIDER=falkor`, `GRAPH_DATABASE_PROVIDER=falkor`, `FALKORDB_HOST=falkordb`, `FALKORDB_PORT=6379`, `DB_PROVIDER=sqlite`.
- Access control off, `ENABLE_BACKEND_ACCESS_CONTROL=false`: with it on, Cognee partitions the graph per tenant, which fragments cross-source resolution and can report zero nodes.
- `COGNEE_SKIP_CONNECTION_TEST=true` to keep startup fast and deterministic.
- A startup guard, `assert_no_openai`, hard-fails if any provider, model, endpoint, or key could route to OpenAI.

## Startup ordering and the environment gotcha

The environment and the no-OpenAI guard are configured before any module imports Cognee. In the container, all configuration arrives as environment variables from Docker Compose; no `.env` file is copied into the image, so Cognee's own dotenv load has nothing to override the injected values with. On host runs, a helper clears Cognee's cached config factories so a stray `.env` cannot pin a stale value.

## Concurrency and reliable ingest

uvicorn runs a single worker so the in-process asyncio primitives are authoritative. A mutation lock serializes ingest, teach, forget, and prune, which prevents interleaved graph writes and Groq rate-limit bursts. A small semaphore throttles concurrent searches. Every LLM-backed call has exponential backoff on rate-limit responses.

Multi-file uploads are ingested one file at a time. Each file is added and then cognified before the next begins, so only one writer touches the graph at any moment. Per-file status moves from queued to processing to in graph, or to failed if it exhausts its retries.

## Warm start and in-app restore

A committed FalkorDB OpenCypher snapshot (`falkor_graph.json`) and SQLite database snapshot (`cognee_db.sqlite`) allow instant warm boot in < 2 seconds with zero Groq LLM calls. The demo can also be restored warm from the committed snapshot inside the running backend whenever an upload has replaced it in the graph.

## Deployment and ports

The system runs with `docker compose up --build`, which starts three services:

- FalkorDB on 6379 (Redis/Cypher protocol) and 3000 (web dashboard).
- Backend API on 8000.
- Frontend on 8080.
- Frontend nginx on 8080, proxying `/api` to the backend.
