"""Approvals tool: pending_actions_list. (The write tools that create actions arrive in week 2.)"""

from __future__ import annotations

import psycopg

from mcp_server.db import rows
from mcp_server.scope import Scope

STATUSES = ("pending", "approved", "rejected", "all")
COLUMNS = """action_id, action_type, branch_id, payload, amount, currency, requested_by, requested_role,
             approver_role, status, created_at, decided_by, decided_at, decision_note"""


def pending_actions_list(conn: psycopg.Connection, scope: Scope, status: str = "pending") -> dict:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    p = {"status": status, "user": scope.user_id, "role": scope.role, "b": list(scope.branches)}
    status_filter = "(%(status)s = 'all' OR status = %(status)s)"
    waiting = []
    if scope.role in ("owner", "area_manager"):
        # Approvers see actions routed to their role within their branches; never their own requests.
        waiting = rows(conn, f"""
            SELECT {COLUMNS} FROM pending_actions
            WHERE {status_filter} AND approver_role = %(role)s AND requested_by <> %(user)s
              AND (branch_id = ANY(%(b)s) OR (branch_id IS NULL AND %(role)s = 'owner'))
            ORDER BY created_at""", p)
    mine = rows(conn, f"""
        SELECT {COLUMNS} FROM pending_actions
        WHERE {status_filter} AND requested_by = %(user)s ORDER BY created_at""", p)
    return {"query_summary": f"actions with status {status} for {scope.user_id}",
            "waiting_for_you": waiting, "requested_by_you": mine}
