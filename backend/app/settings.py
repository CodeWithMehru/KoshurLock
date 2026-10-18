"""Configuration and environment guard for KoshurLock Holmes.

Single place that decides how Cognee talks to the outside world. Three jobs,
run once at startup by :func:`configure`, BEFORE ``import cognee``:

1. ``load_env()``      - read ``.env`` (host/dev only) and fill defaults for every
   setting Cognee needs, then pin the on-disk data/system directories.
2. ``assert_no_openai()`` - HARD-FAIL if anything could route to OpenAI. This is
   the project's core promise: Groq for the LLM, local FastEmbed for embeddings,
   never OpenAI. Ported verbatim from the v1 backup.
3. ``apply_cognee_config()`` - import Cognee (now that env is set) and pin its
   data/system directories.

Storage: FalkorDB (falkordb/falkordb:latest) as the primary Graph + Vector
Context Layer (using cognee-community-hybrid-adapter-falkor and native OpenCypher)
paired with SQLite for lightweight relational metadata.

In the Docker backend, config arrives via compose ``env_file:``/``environment:``;
there is no ``.env`` inside the container, so Cognee's own ``load_dotenv(override=True)``
is a no-op and cannot clobber the injected values.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# --------------------------------------------------------------------------- #
# Paths. settings.py lives at backend/app/settings.py, so backend/ is two up.
# The cognee data/system roots default to local dirs for host runs and are
# overridden to mounted volume paths in the container via env.
# --------------------------------------------------------------------------- #
BACKEND_ROOT: Path = Path(__file__).resolve().parent.parent
DATA_DIR: Path = BACKEND_ROOT / "data"
COGNEE_SYSTEM_DIR: Path = Path(
    os.environ.get("SYSTEM_ROOT_DIRECTORY", str(BACKEND_ROOT / ".cognee_system"))
)
COGNEE_DATA_DIR: Path = Path(
    os.environ.get("DATA_ROOT_DIRECTORY", str(COGNEE_SYSTEM_DIR / "data"))
)

# The shared dataset every trusted piece of evidence is ingested into.
CASE_DATASET = "case_evidence"

# App state (case registry + analyst-uploaded evidence) sits next to the Cognee
# data root, so in Docker it shares the `dataroot` volume and survives restarts.
# In the container COGNEE_DATA_DIR is /data/cognee_data, so APP_STATE_DIR is /data.
APP_STATE_DIR: Path = COGNEE_DATA_DIR.parent
UPLOADS_DIR: Path = APP_STATE_DIR / "uploads"
CASES_FILE: Path = APP_STATE_DIR / "cases.json"

def _resolve_snapshot_dir() -> Path:
    candidates = [
        Path(os.environ.get("SNAPSHOT_DIR", "/snapshots")),
        Path("/app/snapshots"),
        Path(__file__).resolve().parent.parent / "snapshots",
        Path(__file__).resolve().parent.parent.parent / "scripts" / "snapshots",
    ]
    for c in candidates:
        if c.is_dir() and (c / "falkor_graph.json").is_file():
            return c
    return candidates[0]


# The committed warm snapshot, bind-mounted read-only into the backend container
# or baked into the image. Powers the in-app "reload demo" warm restore.
SNAPSHOT_DIR: Path = _resolve_snapshot_dir()

# Defaults applied with os.environ.setdefault - .env / compose always win.
_DEFAULTS: dict[str, str] = {
    # LLM: Groq via LiteLLM
    "LLM_PROVIDER": "custom",
    "LLM_MODEL": "groq/openai/gpt-oss-120b",
    "LLM_TEMPERATURE": "0.0",
    "LLM_MAX_COMPLETION_TOKENS": "16384",
    # json_mode (plain JSON) is far more reliable on Groq's Llama models than the
    # tool/function-calling json_schema path (which Groq mangles -> cognify aborts).
    "LLM_INSTRUCTOR_MODE": "json_mode",
    # Groq free-tier throttling
    "LLM_RATE_LIMIT_ENABLED": "true",
    "LLM_RATE_LIMIT_REQUESTS": "25",
    "LLM_RATE_LIMIT_INTERVAL": "60",
    # Hard per-file ceiling (seconds) on add+cognify during a case ingest.
    # MUST exceed cognee's internal ~240s LLM retry floor, or rate-limited
    # files get cut off mid-backoff and mis-read as hard timeouts.
    "INGEST_FILE_TIMEOUT_S": "360",
    # Embeddings: local FastEmbed (384-dim MiniLM). Dimensions MUST be explicit,
    # otherwise Cognee defaults to 3072 and silently falls back to OpenAI.
    "EMBEDDING_PROVIDER": "fastembed",
    "EMBEDDING_MODEL": "sentence-transformers/all-MiniLM-L6-v2",
    "EMBEDDING_DIMENSIONS": "384",
    # ----- Storage: FalkorDB (Graph + Vector) + SQLite (relational) -----------
    "DB_PROVIDER": "sqlite",
    "DB_NAME": "cognee_db",
    "VECTOR_DB_PROVIDER": "falkor",
    "VECTOR_DB_URL": "falkordb",
    "VECTOR_DB_PORT": "6379",
    "VECTOR_DB_NAME": "koshurlock_holmes",
    "GRAPH_DATABASE_PROVIDER": "falkor",
    "GRAPH_DATABASE_URL": "falkordb",
    "GRAPH_DATABASE_PORT": "6379",
    "GRAPH_DATABASE_NAME": "koshurlock_holmes",
    "FALKORDB_HOST": "falkordb",
    "FALKORDB_PORT": "6379",
    "FALKORDB_GRAPH_NAME": "koshurlock_holmes",
    # Temporal cognify roughly doubles LLM calls during ingest; OFF on Groq free
    # tier. The timeline still works via a graph-completion fallback in ask().
    "TEMPORAL_COGNIFY": "false",
    # Single-user: access control ON partitions the graph per tenant -> 0-node
    # counts and fragmented cross-source reasoning. OFF gives ONE shared graph.
    "ENABLE_BACKEND_ACCESS_CONTROL": "false",
    "CACHING": "false",
    # Startup / logging
    "COGNEE_SKIP_CONNECTION_TEST": "true",
    "LOG_LEVEL": "INFO",
    # Keep FastEmbed quiet
    "FASTEMBED_VERBOSITY": "off",
}


def load_env() -> None:
    """Load ``.env`` (host/dev), apply defaults, and pin local data directories.

    override=False so anything already in the real environment (compose, shell)
    wins over the ``.env`` file. In the container there is no ``.env`` present,
    so this is effectively a no-op and the injected env stands.
    """
    load_dotenv(BACKEND_ROOT.parent / ".env", override=False)

    falkor_host = os.environ.get("FALKORDB_HOST")
    if falkor_host:
        os.environ.setdefault("VECTOR_DB_URL", falkor_host)
        os.environ.setdefault("GRAPH_DATABASE_URL", falkor_host)
    falkor_port = os.environ.get("FALKORDB_PORT")
    if falkor_port:
        os.environ.setdefault("VECTOR_DB_PORT", falkor_port)
        os.environ.setdefault("GRAPH_DATABASE_PORT", falkor_port)

    for key, value in _DEFAULTS.items():
        os.environ.setdefault(key, value)

    COGNEE_SYSTEM_DIR.mkdir(parents=True, exist_ok=True)
    COGNEE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    (COGNEE_SYSTEM_DIR / "databases").mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("SYSTEM_ROOT_DIRECTORY", str(COGNEE_SYSTEM_DIR))
    os.environ.setdefault("DATA_ROOT_DIRECTORY", str(COGNEE_DATA_DIR))


def assert_no_openai() -> None:
    """Raise ``RuntimeError`` if the configuration could reach OpenAI.

    The guard behind the project's "FREE ONLY / never OpenAI" promise. Ported
    verbatim from the v1 backup. Checks the four ways a stray OpenAI dependency
    sneaks in.
    """
    problems: list[str] = []

    llm_provider = os.environ.get("LLM_PROVIDER", "").strip().lower()
    emb_provider = os.environ.get("EMBEDDING_PROVIDER", "").strip().lower()
    llm_model = os.environ.get("LLM_MODEL", "").strip()
    emb_model = os.environ.get("EMBEDDING_MODEL", "").strip()
    emb_dims = os.environ.get("EMBEDDING_DIMENSIONS", "").strip()

    # 1) Provider identity must be our free providers, not openai.
    if llm_provider != "custom" or "openai" in llm_provider:
        problems.append(f"LLM_PROVIDER must be 'custom' (Groq), got {llm_provider!r}")
    if emb_provider != "fastembed":
        problems.append(
            f"EMBEDDING_PROVIDER must be 'fastembed' (local), got {emb_provider!r}"
        )

    # 2) Model prefixes must point at Groq / a local sentence-transformer.
    if not llm_model.startswith("groq/"):
        problems.append(f"LLM_MODEL must start with 'groq/', got {llm_model!r}")
    if "sentence-transformers/" not in emb_model:
        problems.append(
            f"EMBEDDING_MODEL must be a local sentence-transformers model, "
            f"got {emb_model!r}"
        )

    # 3) Dimensions must be explicit & non-OpenAI (3072 == the silent-fallback trap).
    if not emb_dims:
        problems.append("EMBEDDING_DIMENSIONS is unset (would default to 3072/OpenAI)")
    elif emb_dims == "3072":
        problems.append("EMBEDDING_DIMENSIONS=3072 is the OpenAI fallback dimension")

    # 4) No OpenAI key may be present anywhere - LiteLLM could silently prefer it.
    if os.environ.get("OPENAI_API_KEY", "").strip():
        problems.append(
            "OPENAI_API_KEY is present in the environment - unset it so Cognee "
            "cannot fall back to OpenAI"
        )

    # 5) Soft check: a custom endpoint must not point at OpenAI.
    for var in ("LLM_ENDPOINT", "LLM_API_BASE", "EMBEDDING_ENDPOINT"):
        endpoint = os.environ.get(var, "").strip()
        if endpoint and "api.openai.com" in endpoint:
            problems.append(f"{var} points at api.openai.com")

    if problems:
        raise RuntimeError(
            "OpenAI-fallback guard FAILED - refusing to start:\n  - "
            + "\n  - ".join(problems)
        )


def apply_cognee_config() -> None:
    """Import Cognee (now that env is set) and pin its data/system directories.

    Cognee's config surface varies across 1.x point releases, so probe with
    ``hasattr``/``callable`` and fall back to the env vars set in load_env().
    """
    # Register FalkorDB hybrid adapter for Cognee
    try:
        import cognee_community_hybrid_adapter_falkor.register  # noqa: F401
    except ImportError:
        pass

    import cognee  # heavy import; only after env is populated

    cfg = getattr(cognee, "config", None)
    if cfg is not None:
        for attr, value in (
            ("system_root_directory", str(COGNEE_SYSTEM_DIR)),
            ("data_root_directory", str(COGNEE_DATA_DIR)),
        ):
            setter = getattr(cfg, attr, None)
            if callable(setter):
                try:
                    setter(value)
                except Exception:
                    pass


def clear_cognee_db_caches() -> None:
    """Clear Cognee's five lru-cached DB config/engine factories so they re-read
    os.environ. Needed on host runs where Cognee's own load_dotenv(override=True)
    can pin a stale value; call AFTER importing cognee, before any DB access."""
    try:
        from cognee.infrastructure.databases.relational.config import (
            get_relational_config,
        )
        from cognee.infrastructure.databases.relational.create_relational_engine import (
            create_relational_engine,
        )
        from cognee.infrastructure.databases.graph.config import get_graph_config
        from cognee.infrastructure.databases.graph.get_graph_engine import (
            _create_graph_engine,
        )
        from cognee.infrastructure.databases.vector.create_vector_engine import (
            _create_vector_engine,
        )
    except Exception:
        return
    for fn in (get_relational_config, create_relational_engine, get_graph_config,
               _create_graph_engine, _create_vector_engine):
        try:
            fn.cache_clear()
        except Exception:
            pass


def _masked_key() -> str:
    """Return the Groq key as ``gsk_...last4`` (never print the full secret)."""
    key = os.environ.get("LLM_API_KEY", "")
    if not key:
        return "MISSING"
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}****{key[-4:]}"


def db_summary() -> dict[str, str]:
    """The active storage providers, for the /status endpoint and the banner."""
    return {
        "relational": os.environ.get("DB_PROVIDER", "sqlite"),
        "vector": "falkordb",
        "graph": "falkordb",
        "unified": "falkordb",
    }


def configure(verbose: bool = True) -> dict:
    """Load env, verify no-OpenAI, apply Cognee config. Idempotent.

    Returns a summary dict and prints a startup banner (unless verbose=False).
    Call exactly once before using the engine.
    """
    load_env()
    assert_no_openai()
    apply_cognee_config()

    db = db_summary()
    summary = {
        "llm_provider": os.environ.get("LLM_PROVIDER"),
        "llm_model": os.environ.get("LLM_MODEL"),
        "embedding_provider": os.environ.get("EMBEDDING_PROVIDER"),
        "embedding_model": os.environ.get("EMBEDDING_MODEL"),
        "embedding_dimensions": os.environ.get("EMBEDDING_DIMENSIONS"),
        "db": db,
        "system_dir": str(COGNEE_SYSTEM_DIR),
        "groq_key_present": bool(os.environ.get("LLM_API_KEY")),
        "openai_key_present": bool(os.environ.get("OPENAI_API_KEY", "").strip()),
    }

    if verbose:
        emb_short = summary["embedding_model"].split("/")[-1]
        print("=" * 68)
        print("  KOSHURLOCK HOLMES - configuration")
        print("=" * 68)
        print(
            f"[config] LLM    = {summary['llm_provider']} / "
            f"{summary['llm_model']}  (key: {_masked_key()})"
        )
        print(
            f"[config] EMBED  = {summary['embedding_provider']} / {emb_short}  "
            f"dim={summary['embedding_dimensions']}  (LOCAL, no OpenAI network)"
        )
        print(
            f"[config] STORE  = relational:{db['relational']} "
            f"vector:{db['vector']} graph:{db['graph']} "
            f"(unified:{db['unified']})"
        )
        print("[config] OpenAI = NOT PRESENT")
        print("=" * 68)

    return summary


if __name__ == "__main__":
    configure()
