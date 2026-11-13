"""Approval tools.

- `pending_actions_list`: actions waiting for the caller, and the caller's own requests.
- `request_reorder`, `propose_price_change`: write tools. They never change stock or prices; they
  only create a `pending_actions` row routed to the right approver under the reorder and price
  change SOPs.
- `decide_action`: an approver approves or rejects. This is the only way a status changes. The
  agent is never given this tool; only the API calls it, on an explicit click by the approver.
"""

from __future__ import annotations

import json
from decimal import Decimal

import psycopg

from mcp_server.db import EGP_PER_SAR, clean, rows
from mcp_server.scope import NotPermitted, Scope, branch_for

STATUSES = ("pending", "approved", "rejected", "all")
COLUMNS = """action_id, action_type, branch_id, payload, amount, currency, requested_by, requested_role,
             approver_role, status, created_at, decided_by, decided_at, decision_note"""

AREA_APPROVAL_LIMIT_SAR = Decimal("5000")
AREAS_WITH_MANAGER = {"riyadh"}  # reorder SOP: Jeddah and Alexandria have no area manager yet
UNIT_FACTORS = {"g": ("g", 1), "kg": ("g", 1000), "ml": ("ml", 1), "l": ("ml", 1000), "unit": ("unit", 1)}


def pending_actions_list(conn: psycopg.Connection, scope: Scope, status: str = "pending") -> dict:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    p = {"status": status, "user": scope.user_id, "role": scope.role, "b": list(scope.branches)}
    status_filter = "(%(status)s = 'all' OR status = %(status)s)"
    waiting = []
    if scope.role in ("owner", "area_manager"):
        waiting = rows(conn, f"""
            SELECT {COLUMNS} FROM pending_actions
            WHERE {status_filter} AND approver_role = %(role)s
              AND (requested_by <> %(user)s OR (action_type = 'price_change' AND %(role)s = 'owner'))
              AND (branch_id = ANY(%(b)s) OR (branch_id IS NULL AND %(role)s = 'owner'))
            ORDER BY created_at""", p)
    mine = rows(conn, f"""
        SELECT {COLUMNS} FROM pending_actions
        WHERE {status_filter} AND requested_by = %(user)s ORDER BY created_at""", p)
    return {"query_summary": f"actions with status {status} for {scope.user_id}",
            "waiting_for_you": waiting, "requested_by_you": mine}


def reorder_approver(requested_role: str, area: str, amount_sar: Decimal) -> str:
    """Who approves a reorder, per the reorder SOP. Nobody approves their own request."""
    if requested_role == "area_manager":
        return "owner"
    if area in AREAS_WITH_MANAGER and amount_sar <= AREA_APPROVAL_LIMIT_SAR:
        return "area_manager"
    return "owner"


def _one_ingredient(conn: psycopg.Connection, ingredient: str) -> dict:
    key = ingredient.strip()
    found = rows(conn, """
        SELECT g.ingredient_id, g.name_en, g.name_ar, g.unit, g.supplier_id, s.name AS supplier, s.lead_time_days
        FROM ingredients g JOIN suppliers s USING (supplier_id)
        WHERE g.ingredient_id = %(k)s OR g.name_en ILIKE %(like)s OR g.name_ar ILIKE %(like)s
        ORDER BY (g.ingredient_id = %(k)s) DESC, g.ingredient_id""", {"k": key, "like": f"%{key}%"})
    if not found:
        raise ValueError(f"no ingredient matches {ingredient!r}")
    if len(found) > 1 and found[0]["ingredient_id"] != key:
        names = ", ".join(f"{r['ingredient_id']} ({r['name_en']})" for r in found[:8])
        raise ValueError(f"{ingredient!r} matches several ingredients: {names}. Use one ingredient id.")
    return found[0]


def _insert(conn: psycopg.Connection, **values) -> dict:
    values["payload"] = json.dumps(values["payload"], ensure_ascii=False)
    cols = ", ".join(values)
    marks = ", ".join(f"%({k})s" for k in values)
    row = conn.execute(f"INSERT INTO pending_actions ({cols}) VALUES ({marks}) RETURNING {COLUMNS}",
                       values).fetchone()
    return clean(row)


def request_reorder(conn: psycopg.Connection, scope: Scope, branch_id: str, ingredient: str, qty: float,
                    unit: str, note: str | None = None) -> dict:
    branch = branch_for(scope, branch_id)
    if unit not in UNIT_FACTORS:
        raise ValueError(f"unit must be one of {', '.join(UNIT_FACTORS)}")
    if not 0 < qty <= 1_000_000:
        raise ValueError("qty must be greater than 0")
    g = _one_ingredient(conn, ingredient)
    base_unit, factor = UNIT_FACTORS[unit]
    if base_unit != g["unit"]:
        raise ValueError(f"{g['name_en']} is counted in {g['unit']}; give the quantity in "
                         f"{'g or kg' if g['unit'] == 'g' else 'ml or l' if g['unit'] == 'ml' else 'units'}")
    qty_base = Decimal(str(qty)) * factor
    b = conn.execute("SELECT area, currency FROM branches WHERE branch_id = %s", (branch,)).fetchone()
    unit_cost = conn.execute("SELECT unit_cost_at(%s, %s, current_date) AS c",
                             (g["ingredient_id"], b["currency"])).fetchone()["c"]
    if unit_cost is None:
        raise ValueError(f"no cost on record for {g['name_en']} in {b['currency']}")
    amount = (qty_base * unit_cost).quantize(Decimal("0.01"))
    amount_sar = amount / EGP_PER_SAR if b["currency"] == "EGP" else amount
    approver = reorder_approver(scope.role, b["area"], amount_sar)
    payload = {"ingredient_id": g["ingredient_id"], "name_en": g["name_en"], "name_ar": g["name_ar"],
               "qty": float(qty), "unit": unit, "qty_base": float(qty_base), "base_unit": g["unit"],
               "unit_cost": float(unit_cost), "supplier_id": g["supplier_id"], "supplier": g["supplier"],
               "lead_time_days": g["lead_time_days"], "note": (note or "").strip() or None}
    existing = rows(conn, f"""
        SELECT {COLUMNS} FROM pending_actions
        WHERE status = 'pending' AND action_type = 'reorder' AND requested_by = %(u)s AND branch_id = %(b)s
          AND payload->>'ingredient_id' = %(i)s AND amount = %(a)s""",
                    {"u": scope.user_id, "b": branch, "i": g["ingredient_id"], "a": amount})
    notes = [f"Nothing is ordered yet: the {approver.replace('_', ' ')} must approve this request first.",
             f"Amount = {float(qty_base):g} {g['unit']} × {float(unit_cost):g} {b['currency']} per {g['unit']} "
             f"(current supplier cost)."]
    if existing:
        return {"query_summary": f"{branch} · reorder of {g['name_en']} already pending",
                "action": existing[0], "created": False,
                "notes": ["An identical request is already pending; no new request was created."] + notes}
    action = _insert(conn, action_type="reorder", branch_id=branch, payload=payload, amount=amount,
                     currency=b["currency"], requested_by=scope.user_id, requested_role=scope.role,
                     approver_role=approver)
    return {"query_summary": f"{branch} · reorder request #{action['action_id']} · {g['name_en']}",
            "action": action, "created": True, "notes": notes}


def propose_price_change(conn: psycopg.Connection, scope: Scope, item: str, new_price: float, reason: str,
                         currency: str | None = None) -> dict:
    if not reason or len(reason.strip()) < 5:
        raise ValueError("a price change needs a written reason")
    if not 0 < new_price <= 100_000:
        raise ValueError("new_price must be greater than 0")
    mine = [r["currency"] for r in conn.execute(
        "SELECT DISTINCT currency FROM branches WHERE branch_id = ANY(%s) ORDER BY 1",
        (list(scope.branches),)).fetchall()]
    if currency is None:
        if len(mine) > 1:
            raise ValueError("say which price list to change: SAR (B1, B2, B4) or EGP (B3)")
        currency = mine[0]
    currency = currency.strip().upper()
    if currency not in mine:
        raise NotPermitted("You can't change prices in that currency.")
    key = item.strip()
    found = rows(conn, """
        SELECT m.item_id, m.name_en, m.name_ar, p.price AS current_price
        FROM menu_items m
        JOIN LATERAL (SELECT price FROM item_prices ip WHERE ip.item_id = m.item_id AND ip.currency = %(c)s
                      ORDER BY valid_from DESC LIMIT 1) p ON true
        WHERE m.item_id = %(k)s OR m.name_en ILIKE %(like)s OR m.name_ar ILIKE %(like)s
        ORDER BY (m.item_id = %(k)s) DESC, m.item_id""", {"k": key, "like": f"%{key}%", "c": currency})
    if not found:
        raise ValueError(f"no menu item matches {item!r}")
    if len(found) > 1 and found[0]["item_id"] != key:
        names = ", ".join(f"{r['item_id']} ({r['name_en']})" for r in found[:8])
        raise ValueError(f"{item!r} matches several items: {names}. Use one item id.")
    m = found[0]
    change_pct = round(100 * (new_price - m["current_price"]) / m["current_price"], 1)
    payload = {"item_id": m["item_id"], "name_en": m["name_en"], "name_ar": m["name_ar"], "currency": currency,
               "current_price": m["current_price"], "new_price": float(new_price), "change_pct": change_pct,
               "reason": reason.strip()}
    action = _insert(conn, action_type="price_change", branch_id=None, payload=payload, amount=None,
                     currency=None, requested_by=scope.user_id, requested_role=scope.role, approver_role="owner")
    notes = ["Prices don't change yet: the owner must approve. An approved price takes effect the next "
             "business day."]
    if abs(change_pct) > 30:
        notes.append(f"This is a {change_pct:+g}% change; check the new price.")
    return {"query_summary": f"{currency} price list · price change request #{action['action_id']} · {m['name_en']}",
            "action": action, "created": True, "notes": notes}


def decide_action(conn: psycopg.Connection, scope: Scope, action_id: int, decision: str,
                  note: str | None = None) -> dict:
    """Approve or reject one pending action. Refuses anything the caller may not decide, without
    saying whether the action exists."""
    if decision not in ("approved", "rejected"):
        raise ValueError("decision must be approved or rejected")
    refuse = NotPermitted("You can't decide this action.")
    if scope.role not in ("owner", "area_manager"):
        raise refuse
    with conn.transaction():
        a = conn.execute(f"SELECT {COLUMNS} FROM pending_actions WHERE action_id = %s FOR UPDATE",
                         (action_id,)).fetchone()
        if a is None or a["approver_role"] != scope.role:
            raise refuse
        own = a["requested_by"] == scope.user_id
        if own and not (a["action_type"] == "price_change" and scope.role == "owner"):
            raise refuse
        if a["branch_id"] is None and scope.role != "owner":
            raise refuse
        if a["branch_id"] is not None and a["branch_id"] not in scope.branches:
            raise refuse
        if a["status"] != "pending":
            raise ValueError(f"action #{action_id} was already {a['status']}")
        row = conn.execute(f"""
            UPDATE pending_actions SET status = %(s)s, decided_by = %(u)s, decided_at = now(),
                   decision_note = %(n)s
            WHERE action_id = %(id)s RETURNING {COLUMNS}""",
                           {"s": decision, "u": scope.user_id, "n": (note or "").strip() or None,
                            "id": action_id}).fetchone()
    return {"query_summary": f"action #{action_id} {decision} by {scope.user_id}", "action": clean(row)}
