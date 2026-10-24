"""Sales tools: branch_sales, top_items, item_margin, compare_branches."""

from __future__ import annotations

from datetime import date

import psycopg

from mcp_server.db import EGP_PER_SAR, check_range, period_notes, rows, summary
from mcp_server.scope import Scope, branches_for

SALES_BUCKETS = {  # group_by → SQL expression (fixed strings, never user input)
    "hour": "extract(hour FROM s.sold_at)::int",
    "day": "s.sold_on",
    "week": "date_trunc('week', s.sold_on)::date",
    "month": "to_char(s.sold_on, 'YYYY-MM')",
    "category": "m.category",
    "total": "'total'",
}
TOP_METRICS = ("qty", "revenue", "margin")
COMPARE_METRICS = ("revenue", "orders", "avg_ticket", "labor_cost_ratio", "waste_share")

# Recipe cost of each sale line, from the cost period that contains the sale date.
COST_JOIN = """
JOIN item_cost_history h ON h.item_id = s.item_id AND h.currency = s.currency
 AND s.sold_on >= h.valid_from AND (h.valid_to IS NULL OR s.sold_on < h.valid_to)
"""


def branch_sales(conn: psycopg.Connection, scope: Scope, date_from: date, date_to: date,
                 branch_ids: list[str] | None = None, group_by: str = "day") -> dict:
    check_range(date_from, date_to)
    if group_by not in SALES_BUCKETS:
        raise ValueError(f"group_by must be one of {', '.join(SALES_BUCKETS)}")
    branches = branches_for(scope, branch_ids)
    data = rows(conn, f"""
        SELECT s.branch_id, s.currency, {SALES_BUCKETS[group_by]} AS period,
               round(sum(s.line_total), 2) AS revenue, count(*) AS orders,
               round(sum(s.line_total) / count(*), 2) AS avg_ticket
        FROM sales s JOIN menu_items m USING (item_id)
        WHERE s.branch_id = ANY(%(b)s) AND s.sold_on BETWEEN %(f)s AND %(t)s
        GROUP BY 1, 2, 3 ORDER BY 1, 3""", {"b": branches, "f": date_from, "t": date_to})
    return {"query_summary": summary(branches, date_from, date_to, f"revenue, orders, average ticket by {group_by}"),
            "rows": data, "notes": period_notes(conn, branches, date_from, date_to)}


def top_items(conn: psycopg.Connection, scope: Scope, date_from: date, date_to: date,
              branch_ids: list[str] | None = None, metric: str = "revenue", limit: int = 10) -> dict:
    check_range(date_from, date_to)
    if metric not in TOP_METRICS:
        raise ValueError(f"metric must be one of {', '.join(TOP_METRICS)}")
    limit = max(1, min(int(limit), 50))
    branches = branches_for(scope, branch_ids)
    data = rows(conn, f"""
        WITH t AS (
            SELECT s.currency, s.item_id, m.name_en, m.name_ar, m.category,
                   sum(s.qty) AS qty, round(sum(s.line_total), 2) AS revenue,
                   round(sum(s.line_total - s.qty * h.unit_cost), 2) AS margin
            FROM sales s JOIN menu_items m USING (item_id) {COST_JOIN}
            WHERE s.branch_id = ANY(%(b)s) AND s.sold_on BETWEEN %(f)s AND %(t)s
            GROUP BY 1, 2, 3, 4, 5)
        SELECT * FROM (SELECT t.*, row_number() OVER (PARTITION BY currency ORDER BY {metric} DESC) AS rank
                       FROM t) r
        WHERE rank <= %(n)s ORDER BY currency, rank""",
                {"b": branches, "f": date_from, "t": date_to, "n": limit})
    return {"query_summary": summary(branches, date_from, date_to, f"top {limit} items by {metric}, per currency"),
            "rows": data, "notes": period_notes(conn, branches, date_from, date_to)}


def item_margin(conn: psycopg.Connection, scope: Scope, date_from: date, date_to: date,
                item: str = "all", limit: int = 20) -> dict:
    """Price, recipe cost and margin per item, in the currencies of the caller's branches."""
    check_range(date_from, date_to)
    currencies = [r["currency"] for r in conn.execute(
        "SELECT DISTINCT currency FROM branches WHERE branch_id = ANY(%s) ORDER BY 1",
        (list(scope.branches),)).fetchall()]
    if item == "all":
        item_filter, params = "", {}
    else:
        item_filter = ("AND (m.item_id = %(item)s OR m.name_en ILIKE %(like)s OR m.name_ar ILIKE %(like)s)")
        params = {"item": item.strip(), "like": f"%{item.strip()}%"}
    base = f"""
        SELECT m.item_id, m.name_en, m.name_ar, p.currency, p.price, round(h.unit_cost, 2) AS unit_cost,
               round(p.price - h.unit_cost, 2) AS margin,
               round(100 * (p.price - h.unit_cost) / p.price, 1) AS margin_pct
        FROM menu_items m
        JOIN LATERAL (SELECT currency, price FROM item_prices ip
                      WHERE ip.item_id = m.item_id AND ip.valid_from <= %(t)s
                      ORDER BY valid_from DESC) p ON true
        JOIN item_cost_history h ON h.item_id = m.item_id AND h.currency = p.currency
         AND h.valid_from <= %(t)s AND (h.valid_to IS NULL OR h.valid_to > %(t)s)
        WHERE p.currency = ANY(%(c)s) {item_filter}"""
    params |= {"t": date_to, "c": currencies}
    if item == "all":
        data = rows(conn, f"{base} ORDER BY margin_pct, item_id LIMIT %(n)s",
                    params | {"n": max(1, min(int(limit), 200))})
        what = f"lowest-margin items on {date_to}"
    else:
        data = rows(conn, f"{base} ORDER BY item_id, currency", params)
        if not data:
            raise ValueError(f"no menu item matches {item!r}")
        trend = rows(conn, """
            SELECT h.item_id, h.currency, greatest(h.valid_from, %(f)s) AS cost_from,
                   least(coalesce(h.valid_to - 1, %(t)s), %(t)s) AS cost_to, round(h.unit_cost, 2) AS unit_cost
            FROM item_cost_history h
            WHERE h.item_id = ANY(%(items)s) AND h.currency = ANY(%(c)s)
              AND h.valid_from <= %(t)s AND (h.valid_to IS NULL OR h.valid_to > %(f)s)
            ORDER BY 1, 2, 3""", {"f": date_from, "t": date_to, "c": currencies,
                                  "items": sorted({r["item_id"] for r in data})})
        for r in data:
            r["cost_trend"] = [t for t in trend if t["item_id"] == r["item_id"] and t["currency"] == r["currency"]]
        what = f"price, recipe cost and margin on {date_to}, with cost changes since {date_from}"
    return {"query_summary": f"{', '.join(currencies)} menu · {what}", "rows": data,
            "notes": ["Margin = menu price − recipe cost of ingredients (no labour or overheads)."]}


def compare_branches(conn: psycopg.Connection, scope: Scope, metric: str, date_from: date, date_to: date) -> dict:
    check_range(date_from, date_to)
    if metric not in COMPARE_METRICS:
        raise ValueError(f"metric must be one of {', '.join(COMPARE_METRICS)}")
    branches = branches_for(scope, None)
    p = {"b": branches, "f": date_from, "t": date_to}
    data = rows(conn, """
        SELECT b.branch_id, b.name_en, b.currency, b.opened_on,
               coalesce(s.revenue, 0) AS revenue, coalesce(s.orders, 0) AS orders, s.days_with_sales,
               round(s.revenue / nullif(s.orders, 0), 2) AS avg_ticket,
               round(l.cost / nullif(s.revenue, 0), 4) AS labor_cost_ratio,
               round(w.waste / nullif(w.consumed, 0), 4) AS waste_share
        FROM branches b
        LEFT JOIN (SELECT branch_id, round(sum(line_total), 2) AS revenue, count(*) AS orders,
                          count(DISTINCT sold_on) AS days_with_sales
                   FROM sales WHERE sold_on BETWEEN %(f)s AND %(t)s GROUP BY 1) s USING (branch_id)
        LEFT JOIN (SELECT branch_id, sum(cost) AS cost FROM shift_costs
                   WHERE shift_date BETWEEN %(f)s AND %(t)s GROUP BY 1) l USING (branch_id)
        LEFT JOIN (SELECT branch_id, sum(wasted_cost) AS waste, sum(used_cost + wasted_cost) AS consumed
                   FROM inventory_costed WHERE stock_date BETWEEN %(f)s AND %(t)s GROUP BY 1) w USING (branch_id)
        WHERE b.branch_id = ANY(%(b)s) ORDER BY 1""", p)

    period_days = (date_to - date_from).days + 1
    for r in data:
        rate = EGP_PER_SAR if r["currency"] == "EGP" else 1.0
        r["revenue_sar"] = round(r["revenue"] / rate, 2)
        r["avg_ticket_sar"] = round(r["avg_ticket"] / rate, 2) if r["avg_ticket"] else None
        r["partial_period"] = bool(r["days_with_sales"]) and r["days_with_sales"] < period_days
        days = r["days_with_sales"] or 0
        r["revenue_sar_per_day"] = round(r["revenue_sar"] / days, 2) if days else None
        r["orders_per_day"] = round(r["orders"] / days, 1) if days else None
    rank_by = {"revenue": "revenue_sar_per_day", "orders": "orders_per_day", "avg_ticket": "avg_ticket_sar"}
    key = rank_by.get(metric, metric)
    lower_is_better = metric in ("labor_cost_ratio", "waste_share")
    ranked = sorted((r for r in data if r[key] is not None), key=lambda r: r[key], reverse=not lower_is_better)
    for i, r in enumerate(ranked, 1):
        r["rank"] = i
    notes = period_notes(conn, branches, date_from, date_to)
    notes.append(f"Ranked by {key}{' (lower is better)' if lower_is_better else ''}. EGP is converted at "
                 f"{EGP_PER_SAR:g} EGP = 1 SAR. Revenue and orders are ranked per trading day so a "
                 f"partly open branch compares fairly.")
    return {"query_summary": summary(branches, date_from, date_to, f"branches compared by {metric}"),
            "rows": ranked, "notes": notes}
