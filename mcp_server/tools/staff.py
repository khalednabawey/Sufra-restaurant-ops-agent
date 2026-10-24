"""Staff tools: labor_cost_ratio, staff_schedule."""

from __future__ import annotations

from datetime import date, timedelta

import psycopg

from mcp_server.db import check_range, period_notes, rows, summary
from mcp_server.scope import Scope, branch_for, branches_for

ORDERS_PER_STAFF_HOUR = 15  # scheduling SOP target
BUCKETS = {"total": "'total'", "month": "to_char({d}, 'YYYY-MM')", "week": "date_trunc('week', {d})::date"}


def labor_cost_ratio(conn: psycopg.Connection, scope: Scope, date_from: date, date_to: date,
                     branch_ids: list[str] | None = None, group_by: str = "total") -> dict:
    check_range(date_from, date_to)
    if group_by not in BUCKETS:
        raise ValueError(f"group_by must be one of {', '.join(BUCKETS)}")
    branches = branches_for(scope, branch_ids)
    data = rows(conn, f"""
        WITH l AS (
            SELECT branch_id, {BUCKETS[group_by].format(d="shift_date")} AS period, sum(cost) AS cost,
                   sum(hours) AS hours, coalesce(sum(hours) FILTER (WHERE overtime), 0) AS overtime_hours
            FROM shift_costs WHERE branch_id = ANY(%(b)s) AND shift_date BETWEEN %(f)s AND %(t)s GROUP BY 1, 2),
        s AS (
            SELECT branch_id, {BUCKETS[group_by].format(d="sold_on")} AS period, sum(line_total) AS sales
            FROM sales WHERE branch_id = ANY(%(b)s) AND sold_on BETWEEN %(f)s AND %(t)s GROUP BY 1, 2)
        SELECT l.branch_id, b.currency, l.period, round(l.cost, 2) AS labour_cost, round(s.sales, 2) AS sales,
               round(l.cost / nullif(s.sales, 0), 4) AS labour_ratio, round(l.hours, 1) AS hours,
               round(l.overtime_hours, 1) AS overtime_hours
        FROM l JOIN s USING (branch_id, period) JOIN branches b USING (branch_id)
        ORDER BY 1, 3""", {"b": branches, "f": date_from, "t": date_to})
    notes = period_notes(conn, branches, date_from, date_to)
    notes.append("Labour cost = shift hours × hourly rate, overtime at 1.5×. The scheduling SOP targets "
                 "at most 25% of sales and escalates above 30%.")
    return {"query_summary": summary(branches, date_from, date_to, f"labour cost ÷ sales by {group_by}"),
            "rows": data, "notes": notes}


def staff_schedule(conn: psycopg.Connection, scope: Scope, branch_id: str, on: date, days: int = 1) -> dict:
    """Shifts for one branch over 1–14 days. Staff callers get only their own shifts."""
    branch = branch_for(scope, branch_id)
    days = max(1, min(int(days), 14))
    last = on + timedelta(days - 1)
    own = scope.role == "staff"
    p = {"b": branch, "f": on, "t": last, "staff": scope.staff_id}
    shifts = rows(conn, f"""
        SELECT shift_date, staff_id, full_name, job_role, starts_at, ends_at, overtime
        FROM shift_costs
        WHERE branch_id = %(b)s AND shift_date BETWEEN %(f)s AND %(t)s
          {"AND staff_id = %(staff)s" if own else ""}
        ORDER BY shift_date, starts_at, job_role, staff_id""", p)
    result = {"query_summary": f"{branch} · {on} to {last} · {'your shifts' if own else 'shifts and coverage gaps'}",
              "shifts": shifts, "notes": period_notes(conn, [branch], on, last)}
    if not own:
        result["coverage_gaps"] = rows(conn, """
            WITH hours AS (
                SELECT generate_series(%(f)s::timestamp, %(t)s::timestamp + interval '1 day' - interval '1 hour',
                                       interval '1 hour') AS hour),
            orders AS (
                SELECT date_trunc('hour', sold_at) AS hour, count(*) AS orders
                FROM sales WHERE branch_id = %(b)s AND sold_on BETWEEN %(f)s AND %(t)s GROUP BY 1),
            duty AS (
                SELECT h.hour, count(s.shift_id) AS staff_on_duty
                FROM hours h LEFT JOIN shifts s
                  ON s.branch_id = %(b)s AND s.starts_at <= h.hour AND s.ends_at > h.hour
                GROUP BY 1)
            SELECT d.hour, o.orders, d.staff_on_duty, ceil(o.orders / %(target)s::numeric)::int AS staff_needed
            FROM duty d JOIN orders o USING (hour)
            WHERE o.orders > d.staff_on_duty * %(target)s
            ORDER BY 1""", p | {"target": ORDERS_PER_STAFF_HOUR})
        result["notes"].append(f"A coverage gap is an hour with more than {ORDERS_PER_STAFF_HOUR} orders per "
                               "staff member on duty (the scheduling SOP target).")
    return result
