# Tracepoint backend.
# Bakes the FastEmbed model into an image layer so the first request runs fully
# offline (no HuggingFace/Qdrant CDN fetch at request time).
#
# Pinned to Debian bookworm: the bare `python:3.12-slim` tag has rolled to Debian
# trixie, which this stack was never validated on and which breaks a bookworm-
# pinned PGDG apt line. Bookworm is the OS the image was built and tested against
# and has postgresql-client-17 in PGDG.
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FASTEMBED_CACHE_PATH=/opt/models \
    HF_HOME=/opt/models

WORKDIR /app

# redis-tools provides redis-cli for container healthchecks and FalkorDB diagnostics.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates redis-tools \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies. postgres-binary pulls asyncpg + pgvector + psycopg2-binary
# (prebuilt), so no libpq/build toolchain is needed.
COPY requirements.txt .
RUN pip install -r requirements.txt

# Bake the local embedding model (all-MiniLM-L6-v2, 384 dim) into the image.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('sentence-transformers/all-MiniLM-L6-v2')"

# Application code, evidence data, and scripts.
COPY app ./app
COPY data ./data
COPY scripts ./scripts
COPY snapshots /snapshots
COPY snapshots ./snapshots
EXPOSE 8000

# Single worker so the in-process asyncio locks/semaphore are authoritative.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
