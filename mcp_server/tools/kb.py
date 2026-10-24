"""Knowledge-base tool: search_sop (RAG over SOPs, FDA extracts and the menu)."""

from __future__ import annotations

import psycopg

from mcp_server.db import rows
from mcp_server.embeddings import embed, to_pgvector
from mcp_server.scope import Scope


def search_chunks(conn: psycopg.Connection, scope: Scope, vector: list[float],
                  language: str | None = None, limit: int = 4) -> list[dict]:
    """Rank only the chunks the caller's role may read: the role filter runs before ranking."""
    return rows(conn, """
        SELECT doc_id, title, language, source, content,
               round((1 - (embedding <=> %(v)s::vector))::numeric, 4) AS score
        FROM kb_chunks
        WHERE %(role)s = ANY(allowed_roles) AND (%(lang)s::text IS NULL OR language = %(lang)s)
        ORDER BY embedding <=> %(v)s::vector LIMIT %(n)s""",
                {"v": to_pgvector(vector), "role": scope.role, "lang": language, "n": limit})


def search_sop(conn: psycopg.Connection, scope: Scope, query: str, language: str | None = None,
               limit: int = 4) -> dict:
    if not query.strip():
        raise ValueError("query is empty")
    if language not in (None, "ar", "en"):
        raise ValueError("language must be 'ar', 'en' or omitted")
    limit = max(1, min(int(limit), 8))
    chunks = search_chunks(conn, scope, embed([query])[0], language, limit)
    return {"query_summary": f"knowledge base · top {limit} for {query!r}"
                             + (f" in {language}" if language else ""),
            "rows": chunks,
            "notes": ["Company SOPs are in Arabic and English; FDA Food Code extracts are in English only."]}
