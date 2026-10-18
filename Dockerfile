# =============================================================================
# KoshurLock Holmes - All-in-One Railway & Production Dockerfile
# Multi-stage build providing embedded FalkorDB, FastAPI backend, and React UI
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1: Build the React frontend
# -----------------------------------------------------------------------------
FROM node:20-alpine AS frontend-builder
WORKDIR /build

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

# -----------------------------------------------------------------------------
# Stage 2: Extract FalkorDB graph module
# -----------------------------------------------------------------------------
FROM falkordb/falkordb:latest AS falkordb-src

# -----------------------------------------------------------------------------
# Stage 3: Python runtime + embedded FalkorDB + FastAPI backend
# -----------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FASTEMBED_CACHE_PATH=/opt/models \
    HF_HOME=/opt/models \
    STATIC_DIR=/app/static \
    SNAPSHOT_DIR=/app/snapshots

WORKDIR /app

# System dependencies:
# - redis-server provides Redis 8.x in Debian Trixie (python:3.12-slim)
# - libgomp1 is required for OpenMP parallel processing inside falkordb.so
# - redis-tools provides redis-cli for readiness healthchecks
# - curl & ca-certificates for network checks
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        redis-server \
        redis-tools \
        libgomp1 \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# FalkorDB OpenCypher Redis graph engine module
COPY --from=falkordb-src /var/lib/falkordb/bin/falkordb.so /var/lib/falkordb/bin/falkordb.so

# Python dependencies
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Pre-warm local FastEmbed embedding model into image layer for rapid offline startup
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('sentence-transformers/all-MiniLM-L6-v2')"

# Application code, evidence data, and committed warm snapshot
COPY backend/app ./app
COPY backend/data ./data
COPY backend/scripts ./scripts
COPY backend/snapshots /snapshots
COPY backend/snapshots ./snapshots

# Compiled React UI from Stage 1
COPY --from=frontend-builder /build/dist ./static

# Container entrypoint
COPY start.sh ./start.sh
RUN chmod +x ./start.sh && cp ./start.sh /start.sh

EXPOSE 8000

CMD ["/app/start.sh"]
