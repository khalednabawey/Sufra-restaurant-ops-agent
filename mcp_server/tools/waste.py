"""Waste tool: waste_report."""

from __future__ import annotations

from datetime import date

import psycopg

from mcp_server.db import check_range, period_notes, rows, summary
from mcp_server.scope import Scope, branches_for

GROUPS = {  # group_by → (select list, group by)
    "ingredient": ("w.ingredient_id, g.name_en, g.name_ar, g.unit, round(sum(w.qty), 3) AS qty", "1, 2, 3, 4, 5"),
    "reason": ("w.reason", "1, 2"),
    "week": ("date_trunc('week', w.wasted_on)::date AS week", "1, 2"),
}


def waste_report(conn: psycopg.Connection, scope: Scope, date_from: date, date_to: date,
                 branch_ids: list[str] | None = None, group_by: str = "ingredient") -> dict:
    check_range(date_from, date_to)
    if group_by not in GROUPS:
        raise ValueError(f"group_by must be one of {', '.join(GROUPS)}")
    branches = branches_for(scope, branch_ids)
    p = {"b": branches, "f": date_from, "t": date_to}
    totals = rows(conn, """
        SELECT branch_id, currency, round(sum(wasted_cost), 2) AS waste_cost,
               round(sum(used_cost + wasted_cost), 2) AS stock_used_cost,
               round(sum(wasted_cost) / nullif(sum(used_cost + wasted_cost), 0), 4) AS waste_share
        FROM inventory_costed
        WHERE branch_id = ANY(%(b)s) AND stock_date BETWEEN %(f)s AND %(t)s
        GROUP BY 1, 2 ORDER BY 1""", p)
    select, group = GROUPS[group_by]
    detail = rows(conn, f"""
        SELECT w.branch_id, {select}, round(sum(w.cost), 2) AS cost, count(*) AS entries
        FROM waste_costed w JOIN ingredients g USING (ingredient_id)
        WHERE w.branch_id = ANY(%(b)s) AND w.wasted_on BETWEEN %(f)s AND %(t)s
        GROUP BY {group} ORDER BY w.branch_id, cost DESC""", p)
    for t in totals:
        t["breakdown"] = [{k: v for k, v in d.items() if k != "branch_id"}
                          for d in detail if d["branch_id"] == t["branch_id"]][:15]
    notes = period_notes(conn, branches, date_from, date_to)
    notes.append("waste_share = waste cost ÷ cost of stock used (used + wasted). The waste SOP targets "
                 "at most 3% a month and asks for a written explanation above 5% in a week.")
    return {"query_summary": summary(branches, date_from, date_to, f"waste by {group_by}"),
            "rows": totals, "notes": notes}
