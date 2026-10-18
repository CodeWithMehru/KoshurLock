#!/usr/bin/env bash
# Restore the seeded case so the graph is warm and POST /ingest short-circuits to
# 'exists' with zero LLM calls. Uses FalkorDB OpenCypher + SQLite snapshot restore.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
SNAP="$ROOT/scripts/snapshots"
COMPOSE="docker compose"

if [ ! -f "$SNAP/falkor_graph.json" ]; then
  echo "[restore] no snapshot at $SNAP/falkor_graph.json - snapshot file missing."
  exit 1
fi

echo "[restore] restoring FalkorDB knowledge graph and SQLite state in backend ..."
$COMPOSE exec -T backend python -m app.restore

echo "[restore] done. The FalkorDB graph is warm; POST /ingest will report 'exists' with no LLM calls."

