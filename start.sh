#!/usr/bin/env bash
set -e

# Ensure we run from /app if inside container
if [ -d "/app" ]; then
    cd /app
fi

export FALKORDB_HOST="${FALKORDB_HOST:-127.0.0.1}"
export FALKORDB_PORT="${FALKORDB_PORT:-6379}"
export VECTOR_DB_URL="${FALKORDB_HOST}"
export GRAPH_DATABASE_URL="${FALKORDB_HOST}"
export VECTOR_DB_PORT="${FALKORDB_PORT}"
export GRAPH_DATABASE_PORT="${FALKORDB_PORT}"

if [ "$FALKORDB_HOST" = "127.0.0.1" ] || [ "$FALKORDB_HOST" = "localhost" ]; then
    echo "[start.sh] Starting embedded FalkorDB (redis-server + falkordb.so) on port ${FALKORDB_PORT}..."
    redis-server --loadmodule /var/lib/falkordb/bin/falkordb.so --port "${FALKORDB_PORT}" --daemonize yes

    echo "[start.sh] Waiting for FalkorDB to be ready..."
    until redis-cli -p "${FALKORDB_PORT}" ping 2>/dev/null | grep -q "PONG"; do
        sleep 0.2
    done
    echo "[start.sh] FalkorDB is ready (PONG received)."
else
    echo "[start.sh] Using external FalkorDB at ${FALKORDB_HOST}:${FALKORDB_PORT}."
fi

APP_PORT="${PORT:-8000}"
echo "[start.sh] Starting KoshurLock Holmes FastAPI on port ${APP_PORT}..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${APP_PORT}" --workers 1
