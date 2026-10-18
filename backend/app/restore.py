"""In-app warm restore of the demo case from the committed FalkorDB + SQLite snapshot.

Restores:
1. The FalkorDB knowledge graph (koshurlock_holmes) via real OpenCypher MERGE/CREATE
   queries with ZERO LLM calls.
2. The SQLite relational database (data, datasets, dataset_data records).
3. The data-root blobs (cognee_data text files needed for citations and teach/forget).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tarfile
from pathlib import Path

from typing import Tuple

from . import falkor_client, settings

log = logging.getLogger("tracepoint.restore")


def get_snapshot_dir() -> Path:
    candidates = [
        settings.SNAPSHOT_DIR,
        Path("/snapshots"),
        Path("/app/snapshots"),
        Path(__file__).resolve().parent.parent / "snapshots",
        Path(__file__).resolve().parent.parent.parent / "scripts" / "snapshots",
    ]
    for c in candidates:
        if c.is_dir() and (c / "falkor_graph.json").is_file():
            return c
    return settings.SNAPSHOT_DIR


def snapshot_available() -> bool:
    """True if both graph and relational snapshots are present."""
    sdir = get_snapshot_dir()
    graph_snap = sdir / "falkor_graph.json"
    sqlite_snap = sdir / "cognee_db.sqlite"
    sqlite_fallback = sdir / "cognee_db"
    return graph_snap.is_file() and (sqlite_snap.is_file() or sqlite_fallback.is_file())


def _untar_blobs() -> None:
    sdir = get_snapshot_dir()
    blobs = sdir / "dataroot.tgz"
    if not blobs.is_file():
        return
    try:
        settings.APP_STATE_DIR.mkdir(parents=True, exist_ok=True)
        with tarfile.open(blobs, "r:gz") as tf:
            tf.extractall(settings.APP_STATE_DIR)
        log.info("Data-root blobs restored to %s", settings.APP_STATE_DIR)
    except Exception as exc:  # noqa: BLE001
        log.warning("Data-root blob restore skipped: %s", exc)


def _restore_sqlite() -> None:
    """Restore the SQLite relational database for Cognee and synchronize ACL permissions."""
    sdir = get_snapshot_dir()
    sqlite_snap = sdir / "cognee_db.sqlite"
    sqlite_fallback = sdir / "cognee_db"
    src = sqlite_snap if sqlite_snap.is_file() else sqlite_fallback
    if not src.is_file():
        log.warning("SQLite snapshot not found at %s", src)
        return

    db_dir = settings.COGNEE_SYSTEM_DIR / "databases"
    db_dir.mkdir(parents=True, exist_ok=True)
    target = db_dir / "cognee_db"

    try:
        shutil.copyfile(src, target)
        shutil.copyfile(src, db_dir / "cognee_db.db")

        # Align users, datasets ownership, and ACLs in SQLite
        import sqlite3
        import uuid
        from datetime import datetime, timezone

        for db_file in (target, db_dir / "cognee_db.db"):
            con = sqlite3.connect(db_file)
            cur = con.cursor()

            # Ensure default permissions exist
            now_iso = datetime.now(timezone.utc).isoformat()
            std_perms = [("read", uuid.uuid4().hex), ("write", uuid.uuid4().hex),
                         ("delete", uuid.uuid4().hex), ("share", uuid.uuid4().hex)]
            for p_name, p_id in std_perms:
                cur.execute(
                    "INSERT OR IGNORE INTO permissions (id, created_at, updated_at, name) VALUES (?, ?, NULL, ?)",
                    (p_id, now_iso, p_name)
                )

            perms = cur.execute("SELECT id, name FROM permissions").fetchall()

            # Check users
            users = [r[0] for r in cur.execute("SELECT id FROM users").fetchall()]
            if not users:
                default_uid = "af8c143f-e35b-4698-b237-9e2ade34dbdc"
                cur.execute(
                    "INSERT OR IGNORE INTO users (id, email, is_active, is_superuser, is_verified) "
                    "VALUES (?, 'default_user@example.com', 1, 1, 1)",
                    (default_uid,)
                )
                users = [default_uid]

            active_uid = users[0]
            cur.execute("UPDATE datasets SET owner_id = ?", (active_uid,))
            cur.execute("UPDATE data SET owner_id = ?", (active_uid,))

            # Ensure ACLs exist for all datasets and permissions
            datasets = [r[0] for r in cur.execute("SELECT id FROM datasets").fetchall()]
            for u in users:
                for ds in datasets:
                    for p_id, _ in perms:
                        cur.execute(
                            "INSERT OR IGNORE INTO acls (id, created_at, updated_at, principal_id, permission_id, dataset_id) "
                            "VALUES (?, ?, NULL, ?, ?, ?)",
                            (uuid.uuid4().hex, now_iso, u, p_id, ds)
                        )

            con.commit()
            con.close()

        log.info("SQLite relational database restored and ACLs synchronized at %s", target)
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to restore SQLite database: %s", exc)


def _restore_falkor_graph() -> tuple[int, int]:
    """Restore the Northgate Financial graph into FalkorDB using OpenCypher queries."""
    sdir = get_snapshot_dir()
    graph_snap = sdir / "falkor_graph.json"
    if not graph_snap.is_file():
        raise RuntimeError(f"FalkorDB snapshot not found at {graph_snap}")

    with open(graph_snap, encoding="utf-8") as f:
        data = json.load(f)

    nodes = data.get("nodes", [])
    edges = data.get("edges", [])

    # Wipe existing demo graph to start clean
    falkor_client.clear_graph()

    # Sync all nodes and edges via OpenCypher
    n_count, e_count = falkor_client.sync_nodes_and_edges(nodes, edges)
    log.info("Restored FalkorDB graph: %d nodes, %d edges", n_count, e_count)
    return n_count, e_count


async def restore_demo() -> dict:
    """Warm-restore the demo graph and relational state from the committed snapshot.
    Raises if the snapshot is missing or the graph is still empty afterwards."""
    if not snapshot_available():
        raise RuntimeError(f"Demo snapshot not found in {settings.SNAPSHOT_DIR}")

    from . import cognee_engine
    from .settings import clear_cognee_db_caches

    async with cognee_engine._mutate_lock:  # noqa: SLF001
        clear_cognee_db_caches()

        # 1. Restore local blobs and SQLite database
        _untar_blobs()
        _restore_sqlite()

        # 2. Restore FalkorDB graph via OpenCypher (run in thread to keep async loop responsive)
        await asyncio.to_thread(_restore_falkor_graph)

        clear_cognee_db_caches()

        cognee_engine._STATE["purged"] = set()
        from . import cases
        cases.set_materialized(cases.DEMO_ID)
        cases.set_active(cases.DEMO_ID)

    counts = await cognee_engine.graph_counts()
    nodes = counts.get("nodes") or 0
    if not nodes:
        raise RuntimeError(
            "Restore ran but the graph is still empty in FalkorDB."
        )

    return {"status": "restored", **counts}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    res = asyncio.run(restore_demo())
    print("Demo restore completed:", res)

