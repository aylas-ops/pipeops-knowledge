"""Build a SQLite/FTS5 retrieval index over OKF bundles.

Deliberately dependency-free. The pilot needs to prove that grounded, cited
answers beat ungrounded ones -- BM25 over heading-level chunks does that
without anyone provisioning a vector database first. `embed_chunks` is the
seam to swap in embeddings once the pilot earns the budget; the schema
already has somewhere to put them.

Design notes:
  * Chunks are split by heading (manual §9) so a citation can point at
    `concept#heading` rather than a whole document.
  * Every chunk carries its concept's frontmatter, so metadata filters keep
    working after chunking -- the thing naive RAG pipelines lose.
  * Trust tier and staleness are precomputed at index time (SPEC §5.3, §5.5)
    so the query layer can filter on them without re-parsing.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Iterable, Sequence

from .model import Bundle, Concept, format_timestamp, now_utc

SCHEMA_VERSION = 1

_SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS concepts (
    concept_id    TEXT PRIMARY KEY,
    bundle        TEXT NOT NULL,
    path          TEXT NOT NULL,
    type          TEXT,
    title         TEXT,
    description   TEXT,
    resource      TEXT,
    owner         TEXT,
    status        TEXT NOT NULL,
    tags          TEXT NOT NULL DEFAULT '[]',
    generated_by  TEXT,
    generated_at  TEXT,
    verified_at   TEXT,
    trust_tier    TEXT NOT NULL,
    stale_after   TEXT,
    is_stale      INTEGER NOT NULL DEFAULT 0,
    frontmatter   TEXT NOT NULL DEFAULT '{}',
    body          TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS concepts_type   ON concepts(type);
CREATE INDEX IF NOT EXISTS concepts_status ON concepts(status);
CREATE INDEX IF NOT EXISTS concepts_owner  ON concepts(owner);
CREATE INDEX IF NOT EXISTS concepts_bundle ON concepts(bundle);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id   INTEGER PRIMARY KEY,
    concept_id TEXT NOT NULL REFERENCES concepts(concept_id) ON DELETE CASCADE,
    heading    TEXT NOT NULL DEFAULT '',
    ordinal    INTEGER NOT NULL DEFAULT 0,
    text       TEXT NOT NULL,
    embedding  BLOB
);

CREATE INDEX IF NOT EXISTS chunks_concept ON chunks(concept_id);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text,
    heading,
    title,
    description,
    tags,
    tokenize = 'porter unicode61'
);

CREATE TABLE IF NOT EXISTS links (
    src      TEXT NOT NULL,
    dst      TEXT NOT NULL,
    resolved INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sources (
    concept_id    TEXT NOT NULL,
    source_id     TEXT,
    resource      TEXT,
    title         TEXT,
    author        TEXT,
    usage_count   INTEGER,
    last_modified TEXT
);

CREATE INDEX IF NOT EXISTS sources_concept ON sources(concept_id);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _iso(value: dt.datetime | None) -> str | None:
    return format_timestamp(value) if value else None


def _qualified_id(bundle: Bundle, concept: Concept) -> str:
    """`<bundle>/<concept id>` -- unique across a multi-bundle repository."""
    return f"{bundle.name}/{concept.concept_id}"


def build(
    bundles: Sequence[Bundle],
    db_path: Path,
    *,
    now: dt.datetime | None = None,
    include_drafts: bool = True,
) -> dict[str, int]:
    """(Re)build the index from scratch. Returns counts for reporting.

    Drafts are indexed by default but tagged, so the query layer can exclude
    them. Indexing draft or stale documents *without* a status filter is one
    of the manual's named pitfalls (§14) -- the fix is filtering at read time,
    not pretending the documents do not exist.
    """
    now = now or now_utc()
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    connection = connect(db_path)
    try:
        connection.executescript(_SCHEMA)
        counts = {"bundles": 0, "concepts": 0, "chunks": 0, "links": 0, "sources": 0}

        for bundle in bundles:
            counts["bundles"] += 1
            for concept in bundle.concepts:
                if concept.status == "draft" and not include_drafts:
                    continue
                counts["concepts"] += 1
                counts["chunks"] += _insert_concept(connection, bundle, concept, now)
                counts["links"] += _insert_links(connection, bundle, concept)
                counts["sources"] += _insert_sources(connection, bundle, concept)

        connection.executemany(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            [
                ("schema_version", str(SCHEMA_VERSION)),
                ("built_at", format_timestamp(now)),
                ("bundles", json.dumps([bundle.name for bundle in bundles])),
            ],
        )
        connection.commit()
        return counts
    finally:
        connection.close()


def _insert_concept(
    connection: sqlite3.Connection, bundle: Bundle, concept: Concept, now: dt.datetime
) -> int:
    concept_id = _qualified_id(bundle, concept)
    generated = concept.generated or {}
    connection.execute(
        """
        INSERT OR REPLACE INTO concepts (
            concept_id, bundle, path, type, title, description, resource, owner,
            status, tags, generated_by, generated_at, verified_at, trust_tier,
            stale_after, is_stale, frontmatter, body
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            concept_id,
            bundle.name,
            concept.rel_path,
            concept.type,
            concept.title,
            concept.description,
            concept.resource,
            concept.owner,
            concept.status,
            json.dumps(concept.tags),
            generated.get("by"),
            _iso(concept.generated_at),
            _iso(concept.verified_at),
            concept.trust_tier,
            _iso(concept.stale_after),
            int(concept.is_stale(now)),
            json.dumps(concept.frontmatter, default=str),
            concept.body,
        ),
    )

    tags = " ".join(concept.tags)
    written = 0
    for chunk in concept.chunks():
        cursor = connection.execute(
            "INSERT INTO chunks(concept_id, heading, ordinal, text) VALUES (?,?,?,?)",
            (concept_id, chunk.heading, chunk.ordinal, chunk.text),
        )
        # Frontmatter fields ride along in the FTS row so a query like
        # "deploy rollback" still matches a chunk whose body never says
        # "rollback" but whose title does.
        connection.execute(
            """
            INSERT INTO chunks_fts(rowid, text, heading, title, description, tags)
            VALUES (?,?,?,?,?,?)
            """,
            (
                cursor.lastrowid,
                chunk.text,
                chunk.heading,
                concept.title,
                concept.description or "",
                tags,
            ),
        )
        written += 1
    return written


def _insert_links(connection: sqlite3.Connection, bundle: Bundle, concept: Concept) -> int:
    rows = []
    for link in concept.links():
        if link.is_external or link.is_anchor:
            continue
        resolved = concept.resolve(link.target)
        if resolved is None:
            continue
        try:
            destination = f"{bundle.name}/{resolved.relative_to(bundle.root).as_posix()}"
        except ValueError:
            continue
        if destination.endswith(".md"):
            destination = destination[:-3]
        rows.append((_qualified_id(bundle, concept), destination, int(resolved.exists())))
    connection.executemany("INSERT INTO links(src, dst, resolved) VALUES (?,?,?)", rows)
    return len(rows)


def _insert_sources(connection: sqlite3.Connection, bundle: Bundle, concept: Concept) -> int:
    rows = []
    for entry in concept.sources:
        last_modified = entry.get("last_modified")
        rows.append(
            (
                _qualified_id(bundle, concept),
                entry.get("id"),
                entry.get("resource"),
                entry.get("title"),
                entry.get("author"),
                entry.get("usage_count"),
                str(last_modified) if last_modified is not None else None,
            )
        )
    connection.executemany(
        """
        INSERT INTO sources(concept_id, source_id, resource, title, author,
                            usage_count, last_modified)
        VALUES (?,?,?,?,?,?,?)
        """,
        rows,
    )
    return len(rows)


def embed_chunks(db_path: Path, embedder) -> int:  # pragma: no cover - optional path
    """Populate `chunks.embedding` using a caller-supplied embedding function.

    The seam for upgrading the pilot to semantic retrieval. ``embedder`` takes
    a list of strings and returns a list of float lists; wire it to Vertex AI
    Embeddings or any approved enterprise model. Nothing else in the toolchain
    needs to change -- `okf query` reads BM25 today and can rank on a hybrid
    score once this column is filled.
    """
    import array

    connection = connect(Path(db_path))
    try:
        rows = connection.execute(
            "SELECT chunk_id, text FROM chunks WHERE embedding IS NULL"
        ).fetchall()
        if not rows:
            return 0
        vectors = embedder([row["text"] for row in rows])
        connection.executemany(
            "UPDATE chunks SET embedding = ? WHERE chunk_id = ?",
            [
                (sqlite3.Binary(array.array("f", vector).tobytes()), row["chunk_id"])
                for row, vector in zip(rows, vectors)
            ],
        )
        connection.commit()
        return len(rows)
    finally:
        connection.close()


def stats(db_path: Path) -> dict[str, object]:
    connection = connect(Path(db_path))
    try:
        result: dict[str, object] = {
            row["key"]: row["value"] for row in connection.execute("SELECT key, value FROM meta")
        }
        for table in ("concepts", "chunks", "links", "sources"):
            result[table] = connection.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        result["by_status"] = {
            row["status"]: row["n"]
            for row in connection.execute(
                "SELECT status, COUNT(*) AS n FROM concepts GROUP BY status ORDER BY status"
            )
        }
        result["by_trust"] = {
            row["trust_tier"]: row["n"]
            for row in connection.execute(
                "SELECT trust_tier, COUNT(*) AS n FROM concepts GROUP BY trust_tier"
            )
        }
        result["by_type"] = {
            row["type"]: row["n"]
            for row in connection.execute(
                "SELECT type, COUNT(*) AS n FROM concepts GROUP BY type ORDER BY n DESC"
            )
        }
        result["broken_links"] = connection.execute(
            "SELECT COUNT(*) AS n FROM links WHERE resolved = 0"
        ).fetchone()["n"]
        return result
    finally:
        connection.close()
