"""Retrieval over an OKF index, with metadata filters and mandatory citations.

Two of the manual's named pitfalls (§14) are addressed here rather than left
to the caller:

  * *Indexing stale or draft documents without status filters* -- drafts and
    expired documents are excluded unless asked for, and always labelled.
  * *Letting AI answer without citations* -- every result carries the concept
    id, heading, and source-of-truth resource, and `format_context` emits a
    prompt block that makes citing the path of least resistance.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Sequence

from .index import connect

# FTS5 treats these as syntax; a natural-language question should not be
# parsed as a boolean expression.
_FTS_SPECIAL = re.compile(r"""["'()*:^\-]""")

# Questions are mostly function words. Left in, they match every chunk in the
# corpus and drown the terms that actually discriminate.
_STOPWORDS = frozenset(
    """
    a an the and or but if then than that this these those of in on at to for
    from by with without as is are was were be been being do does did doing
    have has had having i we you he she it they them our your their my me us
    what which who whom whose when where why how not no nor so such can could
    should would will shall may might must about into over under again more
    most some any all both each few other own same too very just also there
    here does dont don doesnt didnt isnt arent
    """.split()
)


def to_match_query(text: str) -> str:
    """Turn free text into a safe FTS5 MATCH expression.

    Terms are OR'd so a partial match still ranks rather than returning
    nothing; BM25 sorts out which of them mattered. Stopwords are dropped
    unless that would leave nothing to search for.
    """
    raw = [term for term in _FTS_SPECIAL.sub(" ", text).lower().split() if term]
    terms = [term for term in raw if term not in _STOPWORDS and len(term) > 2]
    if not terms:
        terms = raw
    if not terms:
        return '""'
    return " OR ".join(f'"{term}"' for term in terms)


@dataclass
class Result:
    concept_id: str
    bundle: str
    path: str
    heading: str
    title: str
    type: str | None
    description: str | None
    resource: str | None
    owner: str | None
    status: str
    tags: list[str]
    trust_tier: str
    verified_at: str | None
    stale_after: str | None
    is_stale: bool
    score: float
    snippet: str
    text: str = field(repr=False, default="")

    @property
    def citation(self) -> str:
        anchor = f"#{_anchor(self.heading)}" if self.heading else ""
        return f"{self.concept_id}.md{anchor}"

    @property
    def warnings(self) -> list[str]:
        notes = []
        if self.status == "draft":
            notes.append("DRAFT — not reviewed by an owner")
        if self.status == "deprecated":
            notes.append("DEPRECATED — kept for history, not current")
        if self.is_stale:
            notes.append(f"STALE — past stale_after ({self.stale_after})")
        if self.trust_tier == "unverified":
            notes.append("UNVERIFIED — no recorded verification")
        return notes


def _anchor(heading: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", heading.strip().lower()).strip("-")


def search(
    db_path: Path,
    text: str,
    *,
    types: Sequence[str] | None = None,
    tags: Sequence[str] | None = None,
    owner: str | None = None,
    bundle: str | None = None,
    status: Sequence[str] | None = None,
    include_drafts: bool = False,
    include_stale: bool = False,
    include_deprecated: bool = False,
    limit: int = 8,
) -> list[Result]:
    """Rank chunks by BM25, then apply metadata filters.

    Filters run in SQL rather than post-hoc so `limit` means "the best N
    results you are allowed to see", not "the best N overall, some of which
    got dropped".
    """
    connection = connect(Path(db_path))
    try:
        where: list[str] = []
        params: list[Any] = [to_match_query(text)]

        allowed_status = list(status) if status else None
        if allowed_status is None:
            allowed_status = ["stable"]
            if include_drafts:
                allowed_status.append("draft")
            if include_deprecated:
                allowed_status.append("deprecated")
        where.append(f"c.status IN ({','.join('?' * len(allowed_status))})")
        params.extend(allowed_status)

        if not include_stale:
            where.append("c.is_stale = 0")
        if types:
            where.append(f"c.type IN ({','.join('?' * len(types))})")
            params.extend(types)
        if owner:
            where.append("c.owner = ?")
            params.append(owner)
        if bundle:
            where.append("c.bundle = ?")
            params.append(bundle)
        for tag in tags or []:
            # tags are stored as a JSON array; match the quoted element.
            where.append("c.tags LIKE ?")
            params.append(f'%"{tag}"%')

        params.append(limit)
        sql = f"""
            SELECT
                c.concept_id, c.bundle, c.path, c.type, c.title, c.description,
                c.resource, c.owner, c.status, c.tags, c.trust_tier,
                c.verified_at, c.stale_after, c.is_stale,
                ch.heading, ch.text,
                bm25(chunks_fts, 1.0, 2.0, 3.0, 1.5, 1.5) AS score,
                snippet(chunks_fts, 0, '«', '»', ' … ', 24) AS snippet
            FROM chunks_fts
            JOIN chunks   ch ON ch.chunk_id   = chunks_fts.rowid
            JOIN concepts c  ON c.concept_id  = ch.concept_id
            WHERE chunks_fts MATCH ?
              AND {' AND '.join(where)}
            ORDER BY score
            LIMIT ?
        """
        rows = connection.execute(sql, params).fetchall()
    except sqlite3.OperationalError as exc:
        raise RuntimeError(f"query failed against {db_path}: {exc}") from exc
    finally:
        connection.close()

    return [
        Result(
            concept_id=row["concept_id"],
            bundle=row["bundle"],
            path=row["path"],
            heading=row["heading"],
            title=row["title"],
            type=row["type"],
            description=row["description"],
            resource=row["resource"],
            owner=row["owner"],
            status=row["status"],
            tags=json.loads(row["tags"] or "[]"),
            trust_tier=row["trust_tier"],
            verified_at=row["verified_at"],
            stale_after=row["stale_after"],
            is_stale=bool(row["is_stale"]),
            score=float(row["score"]),
            snippet=row["snippet"],
            text=row["text"],
        )
        for row in rows
    ]


def get_concept(db_path: Path, concept_id: str) -> dict[str, Any] | None:
    connection = connect(Path(db_path))
    try:
        row = connection.execute(
            "SELECT * FROM concepts WHERE concept_id = ?", (concept_id,)
        ).fetchone()
        if row is None:
            row = connection.execute(
                "SELECT * FROM concepts WHERE concept_id LIKE ?", (f"%{concept_id}",)
            ).fetchone()
        if row is None:
            return None
        record = dict(row)
        record["tags"] = json.loads(record.get("tags") or "[]")
        record["frontmatter"] = json.loads(record.get("frontmatter") or "{}")
        record["sources"] = [
            dict(source)
            for source in connection.execute(
                "SELECT * FROM sources WHERE concept_id = ?", (record["concept_id"],)
            )
        ]
        record["links_out"] = [
            dict(link)
            for link in connection.execute(
                "SELECT dst, resolved FROM links WHERE src = ?", (record["concept_id"],)
            )
        ]
        record["links_in"] = [
            link["src"]
            for link in connection.execute(
                "SELECT src FROM links WHERE dst = ?", (record["concept_id"],)
            )
        ]
        return record
    finally:
        connection.close()


def format_context(results: Sequence[Result], *, max_chars: int = 12000) -> str:
    """Render results as a grounding block for an agent prompt.

    The trailing instruction is not decoration: an agent that is handed
    citation ids and told to use them cites reliably, and one that is handed
    bare prose does not.
    """
    if not results:
        return (
            "No OKF documents matched. Say so rather than answering from "
            "general knowledge, and suggest which domain owner to ask."
        )

    blocks: list[str] = []
    budget = max_chars
    for position, result in enumerate(results, start=1):
        warnings = f"\nWARNING: {'; '.join(result.warnings)}" if result.warnings else ""
        heading = f" › {result.heading}" if result.heading else ""
        block = (
            f"[{position}] {result.title}{heading}\n"
            f"    citation: {result.citation}\n"
            f"    type: {result.type or 'unknown'} | owner: {result.owner or 'unassigned'} "
            f"| trust: {result.trust_tier} | status: {result.status}\n"
            f"    source of truth: {result.resource or '(none recorded)'}"
            f"{warnings}\n\n{result.text.strip()}\n"
        )
        if len(block) > budget:
            break
        budget -= len(block)
        blocks.append(block)

    return (
        "The following passages come from the organisation's OKF knowledge "
        "base. Answer only from them.\n\n"
        + "\n---\n".join(blocks)
        + "\n---\n"
        "Cite every claim with the `citation` value of the passage it came "
        "from. Repeat any WARNING attached to a passage you rely on. If the "
        "passages do not answer the question, say so and name the owner to ask."
    )


def render_results(results: Sequence[Result], fmt: str = "text") -> str:
    if fmt == "json":
        return json.dumps([asdict(result) for result in results], indent=2)
    if fmt == "context":
        return format_context(results)

    if not results:
        return "No matching documents."
    lines: list[str] = []
    for position, result in enumerate(results, start=1):
        heading = f" › {result.heading}" if result.heading else ""
        lines.append(f"{position}. {result.title}{heading}")
        lines.append(f"   {result.citation}")
        badges = [
            result.type or "?",
            result.owner or "unowned",
            result.trust_tier,
            result.status,
        ]
        lines.append(f"   [{'] ['.join(badges)}]")
        if result.warnings:
            lines.append(f"   !! {'; '.join(result.warnings)}")
        lines.append(f"   {result.snippet.strip()}")
        lines.append("")
    return "\n".join(lines).rstrip()
