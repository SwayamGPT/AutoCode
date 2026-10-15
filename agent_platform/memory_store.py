"""Long-term memory: lessons and episodes with embeddings, plus the learning policy's statistics.

Two interchangeable stores behind one interface:
- PgVectorStore: Postgres + pgvector (Docker compose provides it, zero config).
- SqliteStore: a file under AUTOCODE_HOME, used when no DATABASE_URL is set (desktop default).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import sqlite3
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

from .config import CONFIG_HOME

logger = logging.getLogger("autocode.memory")

HASH_EMBEDDING = "hash-256"
HASH_DIMENSIONS = 256
PG_CONNECT_TIMEOUT = 3
SEARCH_CANDIDATES = 2000  # sqlite scans at most this many recent rows of one embedding model


@dataclass(frozen=True)
class Memory:
    kind: str  # "lesson" (what to do better at a step) or "episode" (a run's outcome)
    content: str
    requirement: str
    embedding: list[float] = field(repr=False)
    embedding_model: str
    dimension: str = ""
    step: str = ""
    score: float | None = None
    run_id: str = ""
    id: int = 0
    created_at: str = ""
    similarity: float = 0.0

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "dimension": self.dimension,
            "step": self.step,
            "content": self.content,
            "requirement": self.requirement,
            "score": self.score,
            "run_id": self.run_id,
            "created_at": self.created_at,
            "similarity": round(self.similarity, 3),
        }


class MemoryStore(Protocol):
    backend: str

    def add(self, memories: list[Memory]) -> None: ...

    def search(self, embedding: list[float], embedding_model: str, dimension: str | None, limit: int) -> list[Memory]: ...

    def recent(self, limit: int, offset: int = 0) -> list[Memory]: ...

    def delete(self, memory_id: int) -> None: ...

    def clear(self) -> None: ...

    def count(self) -> int: ...

    def arm_stats(self, dimension: str) -> dict[str, tuple[float, float, int, float]]: ...

    def update_arm(self, dimension: str, arm: str, reward: float) -> None: ...

    def all_arm_stats(self) -> list[dict[str, Any]]: ...


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def hash_embedding(text: str) -> list[float]:
    """Dependency-free fallback embedding (hashed bag of words), used when no embedding model is reachable."""
    vector = [0.0] * HASH_DIMENSIONS
    for token in re.findall(r"[a-z0-9_]{3,}", text.lower()):
        digest = hashlib.sha1(token.encode("utf-8")).digest()
        vector[int.from_bytes(digest[:4], "big") % HASH_DIMENSIONS] += 1.0 if digest[4] % 2 else -1.0
    return vector


def arm_row(dimension: str, arm: str, alpha: float, beta: float, pulls: int, reward_sum: float) -> dict[str, Any]:
    return {
        "dimension": dimension,
        "arm": arm,
        "pulls": int(pulls),
        "mean_reward": round(reward_sum / pulls, 3) if pulls else None,
        "expected": round(alpha / (alpha + beta), 3),
    }


COLUMNS = "id, created_at, kind, dimension, step, requirement, content, score, run_id, embedding_model, embedding"


def row_to_memory(row: tuple[Any, ...], similarity: float = 0.0) -> Memory:
    memory_id, created_at, kind, dimension, step, requirement, content, score, run_id, model, embedding = row
    vector = json.loads(embedding) if isinstance(embedding, str) else list(embedding or [])
    return Memory(
        kind=kind,
        content=content,
        requirement=requirement,
        embedding=[float(v) for v in vector],
        embedding_model=model,
        dimension=dimension,
        step=step,
        score=score,
        run_id=run_id,
        id=int(memory_id),
        created_at=str(created_at),
        similarity=similarity,
    )


# --- SQLite -------------------------------------------------------------------------------------------
SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  kind TEXT NOT NULL,
  dimension TEXT NOT NULL DEFAULT '',
  step TEXT NOT NULL DEFAULT '',
  requirement TEXT NOT NULL,
  content TEXT NOT NULL,
  score REAL,
  run_id TEXT NOT NULL DEFAULT '',
  embedding_model TEXT NOT NULL,
  embedding TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS memories_model_dimension ON memories (embedding_model, dimension);
CREATE TABLE IF NOT EXISTS strategy_stats (
  dimension TEXT NOT NULL,
  arm TEXT NOT NULL,
  alpha REAL NOT NULL DEFAULT 1,
  beta REAL NOT NULL DEFAULT 1,
  pulls INTEGER NOT NULL DEFAULT 0,
  reward_sum REAL NOT NULL DEFAULT 0,
  PRIMARY KEY (dimension, arm)
);
"""
# Beta-Bernoulli update with a fractional reward r in [0, 1]: alpha += r, beta += 1 - r.
ARM_UPSERT_SQLITE = (
    "INSERT INTO strategy_stats (dimension, arm, alpha, beta, pulls, reward_sum)"
    " VALUES (:d, :a, 1 + :r, 2 - :r, 1, :r)"
    " ON CONFLICT (dimension, arm) DO UPDATE SET alpha = alpha + :r, beta = beta + 1 - :r,"
    " pulls = pulls + 1, reward_sum = reward_sum + :r"
)


class SqliteStore:
    backend = "sqlite"

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lock = threading.Lock()
        with self.lock, self.connect() as db:
            db.executescript(SQLITE_SCHEMA)

    def connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    def add(self, memories: list[Memory]) -> None:
        rows = [
            (m.kind, m.dimension, m.step, m.requirement, m.content, m.score, m.run_id, m.embedding_model, json.dumps(m.embedding))
            for m in memories
        ]
        with self.lock, self.connect() as db:
            db.executemany(
                "INSERT INTO memories (kind, dimension, step, requirement, content, score, run_id, embedding_model, embedding)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    def search(self, embedding: list[float], embedding_model: str, dimension: str | None, limit: int) -> list[Memory]:
        query = f"SELECT {COLUMNS} FROM memories WHERE embedding_model = ?"
        params: list[Any] = [embedding_model]
        if dimension is not None:
            query += " AND dimension = ?"
            params.append(dimension)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(SEARCH_CANDIDATES)
        with self.lock, self.connect() as db:
            rows = db.execute(query, params).fetchall()
        ranked = [replace(m, similarity=cosine(embedding, m.embedding)) for m in map(row_to_memory, rows)]
        return sorted(ranked, key=lambda m: m.similarity, reverse=True)[:limit]

    def recent(self, limit: int, offset: int = 0) -> list[Memory]:
        with self.lock, self.connect() as db:
            rows = db.execute(f"SELECT {COLUMNS} FROM memories ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        return [row_to_memory(row) for row in rows]

    def delete(self, memory_id: int) -> None:
        with self.lock, self.connect() as db:
            db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))

    def clear(self) -> None:
        with self.lock, self.connect() as db:
            db.execute("DELETE FROM memories")
            db.execute("DELETE FROM strategy_stats")

    def count(self) -> int:
        with self.lock, self.connect() as db:
            return int(db.execute("SELECT count(*) FROM memories").fetchone()[0])

    def arm_stats(self, dimension: str) -> dict[str, tuple[float, float, int, float]]:
        with self.lock, self.connect() as db:
            rows = db.execute(
                "SELECT arm, alpha, beta, pulls, reward_sum FROM strategy_stats WHERE dimension = ?", (dimension,)
            ).fetchall()
        return {arm: (alpha, beta, pulls, reward_sum) for arm, alpha, beta, pulls, reward_sum in rows}

    def update_arm(self, dimension: str, arm: str, reward: float) -> None:
        with self.lock, self.connect() as db:
            db.execute(ARM_UPSERT_SQLITE, {"d": dimension, "a": arm, "r": reward})

    def all_arm_stats(self) -> list[dict[str, Any]]:
        with self.lock, self.connect() as db:
            rows = db.execute(
                "SELECT dimension, arm, alpha, beta, pulls, reward_sum FROM strategy_stats ORDER BY dimension, arm"
            ).fetchall()
        return [arm_row(*row) for row in rows]


# --- Postgres + pgvector ------------------------------------------------------------------------------
PG_SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS memories (
  id BIGSERIAL PRIMARY KEY,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  kind TEXT NOT NULL,
  dimension TEXT NOT NULL DEFAULT '',
  step TEXT NOT NULL DEFAULT '',
  requirement TEXT NOT NULL,
  content TEXT NOT NULL,
  score REAL,
  run_id TEXT NOT NULL DEFAULT '',
  embedding_model TEXT NOT NULL,
  embedding vector NOT NULL
);
CREATE INDEX IF NOT EXISTS memories_model_dimension ON memories (embedding_model, dimension);
CREATE TABLE IF NOT EXISTS strategy_stats (
  dimension TEXT NOT NULL,
  arm TEXT NOT NULL,
  alpha DOUBLE PRECISION NOT NULL DEFAULT 1,
  beta DOUBLE PRECISION NOT NULL DEFAULT 1,
  pulls INTEGER NOT NULL DEFAULT 0,
  reward_sum DOUBLE PRECISION NOT NULL DEFAULT 0,
  PRIMARY KEY (dimension, arm)
);
"""
PG_COLUMNS = "id, created_at, kind, dimension, step, requirement, content, score, run_id, embedding_model, embedding::text"
ARM_UPSERT_PG = (
    "INSERT INTO strategy_stats (dimension, arm, alpha, beta, pulls, reward_sum)"
    " VALUES (%(d)s, %(a)s, 1 + %(r)s, 2 - %(r)s, 1, %(r)s)"
    " ON CONFLICT (dimension, arm) DO UPDATE SET alpha = strategy_stats.alpha + %(r)s,"
    " beta = strategy_stats.beta + 1 - %(r)s, pulls = strategy_stats.pulls + 1,"
    " reward_sum = strategy_stats.reward_sum + %(r)s"
)


def vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{value:.6g}" for value in vector) + "]"


class PgVectorStore:
    # ponytail: one short connection per call and an exact cosine scan filtered by embedding model.
    # Add a connection pool and an HNSW index on a fixed-dimension column once memory reaches ~100k rows.
    backend = "postgres"

    def __init__(self, url: str) -> None:
        import psycopg

        self.psycopg = psycopg
        self.url = url
        with self.connect() as db:
            db.execute(PG_SCHEMA)

    def connect(self) -> Any:
        return self.psycopg.connect(self.url, autocommit=True, connect_timeout=PG_CONNECT_TIMEOUT)

    def add(self, memories: list[Memory]) -> None:
        rows = [
            (m.kind, m.dimension, m.step, m.requirement, m.content, m.score, m.run_id, m.embedding_model, vector_literal(m.embedding))
            for m in memories
        ]
        with self.connect() as db, db.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO memories (kind, dimension, step, requirement, content, score, run_id, embedding_model, embedding)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector)",
                rows,
            )

    def search(self, embedding: list[float], embedding_model: str, dimension: str | None, limit: int) -> list[Memory]:
        literal = vector_literal(embedding)
        query = (
            f"SELECT {PG_COLUMNS}, 1 - (embedding <=> %s::vector) AS similarity FROM memories"
            " WHERE embedding_model = %s AND vector_dims(embedding) = %s"
        )
        params: list[Any] = [literal, embedding_model, len(embedding)]
        if dimension is not None:
            query += " AND dimension = %s"
            params.append(dimension)
        query += " ORDER BY embedding <=> %s::vector LIMIT %s"
        params += [literal, limit]
        with self.connect() as db:
            rows = db.execute(query, params).fetchall()
        return [row_to_memory(row[:-1], float(row[-1])) for row in rows]

    def recent(self, limit: int, offset: int = 0) -> list[Memory]:
        with self.connect() as db:
            rows = db.execute(
                f"SELECT {PG_COLUMNS} FROM memories ORDER BY id DESC LIMIT %s OFFSET %s", (limit, offset)
            ).fetchall()
        return [row_to_memory(row) for row in rows]

    def delete(self, memory_id: int) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM memories WHERE id = %s", (memory_id,))

    def clear(self) -> None:
        with self.connect() as db:
            db.execute("TRUNCATE memories, strategy_stats")

    def count(self) -> int:
        with self.connect() as db:
            return int(db.execute("SELECT count(*) FROM memories").fetchone()[0])

    def arm_stats(self, dimension: str) -> dict[str, tuple[float, float, int, float]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT arm, alpha, beta, pulls, reward_sum FROM strategy_stats WHERE dimension = %s", (dimension,)
            ).fetchall()
        return {arm: (alpha, beta, pulls, reward_sum) for arm, alpha, beta, pulls, reward_sum in rows}

    def update_arm(self, dimension: str, arm: str, reward: float) -> None:
        with self.connect() as db:
            db.execute(ARM_UPSERT_PG, {"d": dimension, "a": arm, "r": reward})

    def all_arm_stats(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT dimension, arm, alpha, beta, pulls, reward_sum FROM strategy_stats ORDER BY dimension, arm"
            ).fetchall()
        return [arm_row(*row) for row in rows]


_stores: dict[str, MemoryStore] = {}
_stores_lock = threading.Lock()


# The Postgres that `docker compose up` starts. With no DATABASE_URL, the desktop app uses it when it is running.
AUTO_DATABASE_URL = "postgresql://autocode:autocode@localhost:5432/autocode"


def open_store(database_url: str) -> MemoryStore:
    """Postgres+pgvector when configured (or auto-detected from docker compose); else a local SQLite file."""
    key = database_url or "auto"
    with _stores_lock:
        if key in _stores:
            return _stores[key]
        store: MemoryStore | None = None
        # "sqlite" forces the local file (tests use it so they never touch a real database).
        url = "" if database_url == "sqlite" else database_url or AUTO_DATABASE_URL
        if url.startswith(("postgres://", "postgresql://")):
            try:
                store = PgVectorStore(url)
            except Exception as exc:  # unreachable DB or missing extension: keep working on SQLite
                if database_url:
                    logger.warning("Postgres memory unavailable (%s); falling back to SQLite", exc)
        _stores[key] = store or SqliteStore(CONFIG_HOME / "memory.db")
        return _stores[key]
