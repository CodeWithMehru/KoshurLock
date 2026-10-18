#!/usr/bin/env bash
# Save the seeded case so the app starts warm without re-ingesting (the Groq
# token saver). Exports FalkorDB graph nodes and edges to falkor_graph.json
# and dumps the SQLite database and dataroot blobs.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
SNAP="$ROOT/scripts/snapshots"
mkdir -p "$SNAP"
COMPOSE="docker compose"

echo "[snapshot] exporting FalkorDB graph & SQLite state from backend container ..."
$COMPOSE exec -T backend python -c "
import json
from app.falkor_client import get_graph_data
nodes, edges = get_graph_data()
with open('/snapshots/falkor_graph.json', 'w') as f:
    json.dump({'nodes': nodes, 'edges': edges}, f, indent=2)
print(f'Exported {len(nodes)} nodes, {len(edges)} edges to /snapshots/falkor_graph.json')
"

if $COMPOSE exec -T backend true >/dev/null 2>&1; then
  echo "[snapshot] archiving Cognee data-root blobs ..."
  if $COMPOSE exec -T backend tar czf - -C /data . > "$SNAP/dataroot.tgz" 2>/dev/null; then
    echo "[snapshot] wrote $SNAP/dataroot.tgz"
  else
    echo "[snapshot] data-root archive skipped"
  fi
fi
echo "[snapshot] done."

