"""Inventory tool: low_stock_items."""

from __future__ import annotations

import psycopg

from mcp_server.db import rows
from mcp_server.scope import Scope, branch_for


def low_stock_items(conn: psycopg.Connection, scope: Scope, branch_id: str,
                    days_of_cover_threshold: float = 2.0) -> dict:
    """Items whose latest closing stock covers fewer days than the threshold.

    Days of cover = closing stock ÷ average daily use over the last 14 days.
    """
    if not 0 < days_of_cover_threshold <= 60:
        raise ValueError("days_of_cover_threshold must be between 0 and 60")
    branch = branch_for(scope, branch_id)
    as_of = conn.execute("SELECT max(stock_date) AS d FROM inventory_daily WHERE branch_id = %s",
                         (branch,)).fetchone()["d"]
    data = rows(conn, """
        WITH c AS (
            SELECT ingredient_id, stock_date, closing_qty,
                   avg(used_qty) OVER (PARTITION BY ingredient_id ORDER BY stock_date
                                       ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) AS avg_daily_use
            FROM inventory_daily
            WHERE branch_id = %(b)s AND stock_date > %(d)s::date - 20)
        SELECT c.ingredient_id, g.name_en, g.name_ar, g.unit, c.closing_qty,
               round(c.avg_daily_use, 3) AS avg_daily_use,
               round(c.closing_qty / c.avg_daily_use, 2) AS days_of_cover,
               s.name AS supplier, s.lead_time_days
        FROM c JOIN ingredients g USING (ingredient_id) JOIN suppliers s USING (supplier_id)
        WHERE c.stock_date = %(d)s AND c.avg_daily_use > 0
          AND c.closing_qty / c.avg_daily_use < %(thr)s
        ORDER BY days_of_cover""", {"b": branch, "d": as_of, "thr": days_of_cover_threshold})
    return {"query_summary": f"{branch} · stock on {as_of} · items below {days_of_cover_threshold:g} days of cover",
            "as_of": as_of.isoformat() if as_of else None, "rows": data,
            "notes": ["Days of cover = closing stock ÷ average daily use over the last 14 days."]}
