"""FalkorDB OpenCypher Client and Graph Reasoning Engine.

Direct client for FalkorDB (falkordb/falkordb:latest) providing:
- Connection and graph selection for 'koshurlock_holmes'
- Real OpenCypher queries for multi-hop correlation, alibi verification,
  and degree centrality
- Surgical source retraction (DETACH DELETE for discredited evidence)
- Ingestion and synchronization between Cognee entities and FalkorDB
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from falkordb import FalkorDB

log = logging.getLogger("tracepoint.falkor")

FALKORDB_HOST = os.environ.get("FALKORDB_HOST", "falkordb")
FALKORDB_PORT = int(os.environ.get("FALKORDB_PORT", "6379"))
FALKORDB_GRAPH_NAME = os.environ.get("FALKORDB_GRAPH_NAME", "koshurlock_holmes")

# Classification cues matching graph_transform SOC ontology
_CATEGORY_KEYWORDS = {
    "Person": ("person", "employee", "user ", "analyst", "staff", "manager",
               "whistleblower", "individual", "people", "attacker", "actor", "human"),
    "Account": ("account", "credential", "session", "login", "mailbox",
                "email address", "username", "identity"),
    "Device": ("device", "host", "camera", "cctv", "laptop", "workstation",
               "turnstile", "server", "machine", "endpoint", "gateway", "badge reader"),
    "IP": ("ip address", "ip", "address", "source ip"),
    "File": ("file", "document", "database", "attachment", "spreadsheet",
             "export", "dataset", "customer list"),
    "Location": ("location", "zone", "office", "door", "site", "floor", "city",
                 "building", "wing", "lobby", "egress", "geolocation", "country",
                 "residential", "kitchenette"),
    "Event": ("event", "login attempt", "download", "email", "badge", "detection",
              "incident", "activity", "action", "exfiltration", "resignation",
              "phishing", "logout"),
}
_IPV4 = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")
_ISO_TS = re.compile(r"\b\d{4}-\d{2}-\d{2}[t ]\d{2}:\d{2}", re.IGNORECASE)
_FILE_EXT = re.compile(r"\.(csv|xlsx|docx|pdf|txt|zip|json)\b", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def classify_entity(name: str, entitytype_name: Optional[str] = None, raw_type: str = "Entity") -> str:
    rt = (raw_type or "").strip().lower()
    if rt == "event":
        return "Event"
    hay = " ".join(x for x in (entitytype_name or "", name or "") if x).lower()
    for cat in ("IP", "File", "Person", "Account", "Device", "Location", "Event"):
        if any(c in hay for c in _CATEGORY_KEYWORDS[cat]):
            if cat == "IP" and not re.search(r"\bip\b", hay) and not _IPV4.search(name or ""):
                continue
            return cat
    n = name or ""
    if _IPV4.search(n):
        return "IP"
    if _FILE_EXT.search(n) or "database" in n.lower() or "customer_" in n.lower():
        return "File"
    if _EMAIL.search(n):
        return "Account"
    if _ISO_TS.search(n):
        return "Event"
    return "Other"


def sanitize_ident(name: str, default: str = "Entity") -> str:
    clean = re.sub(r"[^a-zA-Z0-9_]", "_", str(name or default))
    if not clean or not clean.strip("_"):
        clean = default
    if clean[0].isdigit():
        clean = "ID_" + clean
    return clean


def sanitize_rel(rel_name: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_]", "_", str(rel_name or "RELATED")).upper()
    if not clean or not clean.strip("_"):
        clean = "RELATED"
    if clean[0].isdigit():
        clean = "REL_" + clean
    return clean


def _extract_node_info(item: Any) -> tuple[str, str, str, dict]:
    """Extract (id, name, type, properties_dict) safely from any node format."""
    if isinstance(item, (list, tuple)) and len(item) >= 2:
        nid = str(item[0])
        p = item[1] or {}
        name = ""
        raw_type = "Entity"
    elif isinstance(item, dict):
        nid = str(item.get("id") or "")
        p = item.get("properties") or {}
        name = str(item.get("name") or "")
        raw_type = str(item.get("type") or "Entity")
    else:
        return "", "", "", {}

    if isinstance(p, str):
        try:
            p = json.loads(p)
        except Exception:
            try:
                p = json.loads(bytes(p, "utf-8").decode("unicode_escape"))
            except Exception:
                p = {}
    props = dict(p) if isinstance(p, dict) else {}

    if not name and "name" in props:
        name = str(props["name"] or "")
    if (not raw_type or raw_type == "Entity") and "type" in props:
        raw_type = str(props["type"] or "Entity")

    return nid, name, raw_type, props


def _extract_edge_info(item: Any) -> tuple[str, str, str, dict]:
    """Extract (source_id, target_id, rel_name, properties_dict) safely from any edge format."""
    if isinstance(item, (list, tuple)) and len(item) >= 3:
        src = str(item[0] or "")
        tgt = str(item[1] or "")
        rel = str(item[2] or "RELATED")
        eprops = dict(item[3]) if len(item) > 3 and isinstance(item[3], dict) else {}
    elif isinstance(item, dict):
        src = str(item.get("source_id") or item.get("source") or "")
        tgt = str(item.get("target_id") or item.get("target") or "")
        rel = str(item.get("relationship_name") or item.get("label") or "RELATED")
        p = item.get("properties") or {}
        if isinstance(p, str):
            try:
                p = json.loads(p)
            except Exception:
                try:
                    p = json.loads(bytes(p, "utf-8").decode("unicode_escape"))
                except Exception:
                    p = {}
        eprops = dict(p) if isinstance(p, dict) else {}
    else:
        return "", "", "", {}
    return src, tgt, rel, eprops


def get_falkor_client(host: Optional[str] = None, port: Optional[int] = None) -> FalkorDB:
    h = host or os.environ.get("FALKORDB_HOST", FALKORDB_HOST)
    p = port or int(os.environ.get("FALKORDB_PORT", FALKORDB_PORT))
    return FalkorDB(host=h, port=p)


def get_graph(graph_name: Optional[str] = None):
    client = get_falkor_client()
    name = graph_name or os.environ.get("FALKORDB_GRAPH_NAME", FALKORDB_GRAPH_NAME)
    return client.select_graph(name)


def ping() -> bool:
    """Test live connectivity to FalkorDB."""
    try:
        client = get_falkor_client()
        return bool(client.connection.ping())
    except Exception as exc:  # noqa: BLE001
        log.warning("FalkorDB ping failed: %s", exc)
        return False


def query(cypher_str: str, params: Optional[dict] = None, graph_name: Optional[str] = None) -> Any:
    """Execute a Cypher query on FalkorDB."""
    g = get_graph(graph_name)
    return g.query(cypher_str, params or {})


def clear_graph(graph_name: Optional[str] = None) -> None:
    """Wipe all nodes and edges from the graph."""
    try:
        g = get_graph(graph_name)
        g.delete()
    except Exception:
        # Fallback to cypher delete
        try:
            query("MATCH (n) DETACH DELETE n", graph_name=graph_name)
        except Exception as exc:  # noqa: BLE001
            log.warning("clear_graph error: %s", exc)


def sync_nodes_and_edges(
    nodes: list,
    edges: list,
    graph_name: Optional[str] = None,
) -> Tuple[int, int]:
    """Ingest nodes and edges into FalkorDB with full typed labels and Cypher relationships."""
    g = get_graph(graph_name)

    # 1. Map EntityType nodes
    type_map: dict[str, str] = {}
    for item in nodes:
        nid, name, raw_type, props = _extract_node_info(item)
        if not nid:
            continue
        if raw_type.lower() == "entitytype" or str(props.get("type", "")).lower() == "entitytype":
            type_name = name or str(props.get("name", ""))
            type_map[nid] = type_name

    for item in edges:
        src, tgt, rel, _ = _extract_edge_info(item)
        if not src or not tgt:
            continue
        if tgt in type_map:
            type_map[src] = type_map[tgt]
        elif src in type_map:
            type_map.setdefault(tgt, type_map[src])

    # 2. Ingest nodes
    node_count = 0
    for item in nodes:
        nid, name, raw_type, props = _extract_node_info(item)
        if not nid:
            continue

        clean_props: dict[str, Any] = {
            "id": nid,
            "name": name,
            "type": raw_type,
            "raw_type": raw_type,
        }

        # Coerce properties for FalkorDB primitive constraints
        for k, v in props.items():
            if v is not None:
                if isinstance(v, (str, int, float, bool)):
                    clean_props[k] = v
                elif isinstance(v, list) and all(isinstance(x, (str, int, float, bool)) for x in v):
                    clean_props[k] = v
                else:
                    clean_props[k] = json.dumps(v)

        labels = [sanitize_ident(raw_type)]
        if raw_type.lower() == "entity":
            domain_cat = classify_entity(name, type_map.get(nid), raw_type)
            labels.append(sanitize_ident(domain_cat))
            clean_props["domain_type"] = domain_cat

        lbl_str = ":".join(labels)
        cypher = f"MERGE (n:{lbl_str} {{id: $id}}) SET n += $props"
        g.query(cypher, {"id": nid, "props": clean_props})
        node_count += 1

    # 3. Ingest edges
    edge_count = 0
    for item in edges:
        src, tgt, rel, eprops = _extract_edge_info(item)
        if not src or not tgt:
            continue

        clean_rel = sanitize_rel(rel)
        clean_eprops: dict[str, Any] = {
            "source_node_id": src,
            "target_node_id": tgt,
            "relationship_name": rel,
        }
        for k, v in eprops.items():
            if v is not None:
                if isinstance(v, (str, int, float, bool)):
                    clean_eprops[k] = v
                elif isinstance(v, list) and all(isinstance(x, (str, int, float, bool)) for x in v):
                    clean_eprops[k] = v
                else:
                    clean_eprops[k] = json.dumps(v)

        cypher = f"""
        MATCH (a {{id: $src}}), (b {{id: $tgt}})
        MERGE (a)-[r:{clean_rel}]->(b)
        SET r += $props
        """
        g.query(cypher, {"src": src, "tgt": tgt, "props": clean_eprops})
        edge_count += 1

    return node_count, edge_count


def get_graph_data(graph_name: Optional[str] = None) -> Tuple[list, list]:
    """Retrieve all nodes and edges from FalkorDB in Cognee-compatible format:
    nodes = [(id, props), ...]
    edges = [(src, tgt, rel_name, props), ...]
    """
    g = get_graph(graph_name)
    nodes_res = g.query("MATCH (n) RETURN n.id AS id, properties(n) AS props")
    nodes = []
    for record in nodes_res.result_set:
        nid = record[0] or (record[1].get("id") if record[1] else None)
        props = record[1] or {}
        if nid:
            nodes.append((str(nid), props))

    edges_res = g.query("MATCH (n)-[r]->(m) RETURN n.id AS src, m.id AS tgt, type(r) AS rel, properties(r) AS props")
    edges = []
    for record in edges_res.result_set:
        src = record[0]
        tgt = record[1]
        rel = record[2]
        props = record[3] or {}
        if src and tgt:
            edges.append((str(src), str(tgt), str(rel), props))

    return nodes, edges


def find_multi_hop_paths(
    source_id: str,
    target_id: str,
    max_hops: int = 4,
    limit: int = 25,
    graph_name: Optional[str] = None,
) -> list:
    """Find multi-hop paths connecting two nodes using OpenCypher."""
    cypher = f"""
    MATCH p = (a)-[*1..{max_hops}]-(b)
    WHERE a.id = $source_id AND b.id = $target_id
    RETURN p LIMIT {limit}
    """
    res = query(cypher, {"source_id": source_id, "target_id": target_id}, graph_name=graph_name)
    return res.result_set


def get_centrality_and_iocs(graph_name: Optional[str] = None) -> dict:
    """Compute node degree centrality and detect Indicators of Compromise (IoCs)."""
    g = get_graph(graph_name)
    cypher = """
    MATCH (n)
    OPTIONAL MATCH (n)-[r]-()
    RETURN n.id AS id, n.name AS name, n.type AS type, count(r) AS degree
    ORDER BY degree DESC
    """
    res = g.query(cypher)
    degrees: dict[str, int] = {}
    iocs: list[dict] = []

    ioc_markers = ("41.220.13.7", "customer_database", "customer_pii", "soc-2026-0619", "phish")

    for rec in res.result_set:
        nid, name, ntype, deg = str(rec[0]), str(rec[1] or ""), str(rec[2] or ""), int(rec[3] or 0)
        degrees[nid] = deg
        if any(m in name.lower() for m in ioc_markers):
            iocs.append({
                "id": nid,
                "name": name,
                "type": ntype,
                "degree": deg,
                "indicator": True,
            })

    return {"degrees": degrees, "iocs": iocs}


def retract_source(source_name: str, graph_name: Optional[str] = None) -> int:
    """Surgically retract / forget all nodes and edges tied to a discredited source (e.g. anonymous_tip.txt).

    Uses OpenCypher:
    MATCH (n) WHERE n.source = $source_name ... DETACH DELETE n
    """
    g = get_graph(graph_name)

    # Clean nodes matching source tag or node_set
    cypher = """
    MATCH (n)
    WHERE n.source = $source_name
       OR n.source_node_set = $source_name
       OR n.belongs_to_set = $source_name
       OR (n.name = $source_name AND labels(n) = ['NodeSet'])
       OR (n.belongs_to_set IS NOT NULL AND $source_name IN n.belongs_to_set)
       OR ('Entity' IN labels(n) AND toLower(n.name) = 'anonymous tip')
    DETACH DELETE n
    """
    res = g.query(cypher, {"source_name": source_name})
    log.info("Surgically retracted source %s from FalkorDB: %s", source_name, res.header)

    # Return remaining node count
    counts = g.query("MATCH (n) RETURN count(n)")
    return counts.result_set[0][0] if counts.result_set else 0


def record_analyst_finding(text: str, graph_name: Optional[str] = None) -> None:
    """Store an analyst finding in FalkorDB as :AnalystFinding and :Entity."""
    g = get_graph(graph_name)
    clean_props = {
        "id": "finding_soc_2026_0619",
        "name": "SOC-2026-0619 Analyst Finding",
        "finding": text,
        "text": text,
        "reliability": "CONFIRMED",
        "source": "analyst_correction",
        "source_node_set": "analyst_correction",
        "type": "AnalystFinding",
    }
    cypher = """
    MERGE (n:AnalystFinding {id: $id})
    SET n:Entity, n += $props
    """
    g.query(cypher, {"id": clean_props["id"], "props": clean_props})
    log.info("Recorded AnalystFinding in FalkorDB: %s", clean_props["name"])


def query_graph_context(question: str, graph_name: Optional[str] = None) -> dict:
    """Retrieve all relevant evidence chunks, entities, graph relationships,
    and analyst findings directly from FalkorDB via OpenCypher."""
    g = get_graph(graph_name)

    # 1. Retrieve all DocumentChunk texts (excluding empty or retracted ones)
    chunks: list[dict] = []
    try:
        chunks_res = g.query("""
        MATCH (c:DocumentChunk)
        WHERE c.text IS NOT NULL AND c.text <> ''
        RETURN c.source_node_set AS source, c.text AS text
        """)
        for r in chunks_res.result_set:
            if r[1]:
                chunks.append({"source": r[0] or "unknown", "text": str(r[1])})
    except Exception as exc:
        log.warning("FalkorDB query_graph_context chunks error: %s", exc)

    # 2. Retrieve any AnalystFinding nodes taught via teach()
    findings: list[dict] = []
    try:
        findings_res = g.query("""
        MATCH (f:AnalystFinding)
        RETURN f.name AS name, f.finding AS finding, f.text AS text
        """)
        for r in findings_res.result_set:
            findings.append({
                "name": str(r[0] or "SOC Determination"),
                "text": str(r[2] or r[1] or ""),
            })
    except Exception as exc:
        log.warning("FalkorDB query_graph_context findings error: %s", exc)

    # 3. Retrieve Entities and their relationships (limited for token budget)
    relationships: list[str] = []
    try:
        rel_res = g.query("""
        MATCH (a:Entity)-[r]->(b:Entity)
        RETURN a.name AS source_name, type(r) AS rel_type, b.name AS target_name
        LIMIT 35
        """)
        for r in rel_res.result_set:
            if r[0] and r[2]:
                relationships.append(f"({r[0]}) -[{r[1]}]-> ({r[2]})")
    except Exception as exc:
        log.warning("FalkorDB query_graph_context rels error: %s", exc)

    has_anonymous_tip = any("anonymous_tip.txt" in c["source"] for c in chunks)
    has_finding = len(findings) > 0

    parts: list[str] = []
    if findings:
        parts.append("### CONFIRMED ANALYST FINDINGS & SOC DETERMINATIONS (HIGHEST PRIORITY TRUTH):")
        for f in findings:
            parts.append(f"- [{f['name']}]: {f['text']}\n")

    if chunks:
        parts.append("### ACTIVE CASE EVIDENCE & SOURCE LOGS:")
        for c in chunks:
            parts.append(f"--- SOURCE: {c['source']} ---\n{c['text']}\n")

    if relationships:
        parts.append("### KNOWLEDGE GRAPH RELATIONSHIPS:")
        parts.append("\n".join(relationships[:30]))

    context_str = "\n\n".join(parts)
    return {
        "chunks": chunks,
        "findings": findings,
        "relationships": relationships,
        "has_anonymous_tip": has_anonymous_tip,
        "has_finding": has_finding,
        "context_str": context_str,
    }

