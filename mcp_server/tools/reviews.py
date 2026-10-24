"""Reviews tool: reviews_summary."""

from __future__ import annotations

from datetime import date

import psycopg

from mcp_server.db import check_range, period_notes, rows, summary
from mcp_server.scope import Scope, branch_for

QUOTE_CHARS = 220


def reviews_summary(conn: psycopg.Connection, scope: Scope, branch_id: str, date_from: date, date_to: date) -> dict:
    check_range(date_from, date_to)
    branch = branch_for(scope, branch_id)
    p = {"b": branch, "f": date_from, "t": date_to}
    stats = rows(conn, """
        SELECT count(*) AS reviews, count(*) FILTER (WHERE sentiment = 'positive') AS positive,
               count(*) FILTER (WHERE sentiment = 'negative') AS negative,
               round(count(*) FILTER (WHERE sentiment = 'positive')::numeric / nullif(count(*), 0), 4)
                   AS positive_share
        FROM reviews WHERE branch_id = %(b)s AND reviewed_on BETWEEN %(f)s AND %(t)s""", p)[0]
    samples = rows(conn, """
        SELECT * FROM (
            SELECT sentiment, reviewed_on, left(body_ar, %(n)s) AS quote,
                   row_number() OVER (PARTITION BY sentiment ORDER BY md5(review_id::text)) AS k
            FROM reviews WHERE branch_id = %(b)s AND reviewed_on BETWEEN %(f)s AND %(t)s) r
        WHERE k <= 3 ORDER BY sentiment DESC, k""", p | {"n": QUOTE_CHARS})
    notes = period_notes(conn, [branch], date_from, date_to)
    notes.append("Illustrative reviews from a public research corpus of Arabic restaurant reviews "
                 "(ElSahar & El-Beltagy, 2015), attached to branches at random.")
    return {"query_summary": summary([branch], date_from, date_to, "review count, positive share, sample quotes"),
            **stats, "samples": [{k: v for k, v in s.items() if k != "k"} for s in samples], "notes": notes}
