"""Every tool filters by the caller's scope. Needs the seeded database."""

from datetime import date

import pytest

from mcp_server.scope import NotPermitted
from mcp_server.tools import actions, inventory, kb, reviews, sales, staff, waste
from tests.conftest import scope_of

MAY = {"date_from": date(2026, 5, 1), "date_to": date(2026, 5, 31)}

# (tool function, kwargs that ask for branch B1)
ASK_FOR_B1 = [
    (sales.branch_sales, MAY | {"branch_ids": ["B1"]}),
    (sales.top_items, MAY | {"branch_ids": ["B1"]}),
    (waste.waste_report, MAY | {"branch_ids": ["B1"]}),
    (staff.labor_cost_ratio, MAY | {"branch_ids": ["B2", "B1"]}),
    (inventory.low_stock_items, {"branch_id": "B1"}),
    (staff.staff_schedule, {"branch_id": "B1", "on": date(2026, 5, 3)}),
    (reviews.reviews_summary, MAY | {"branch_id": "B1"}),
]


@pytest.mark.parametrize("fn,kwargs", ASK_FOR_B1, ids=[f.__name__ for f, _ in ASK_FOR_B1])
def test_branch_manager_is_refused_another_branch(conn, fn, kwargs):
    with pytest.raises(NotPermitted):
        fn(conn, scope_of("u_bm_tahlia"), **kwargs)


@pytest.mark.parametrize("user_id,expected", [
    ("u_owner", {"B1", "B2", "B3", "B4"}),
    ("u_area_riyadh", {"B1", "B4"}),
    ("u_bm_tahlia", {"B2"}),
])
def test_default_queries_return_only_rows_in_scope(conn, user_id, expected):
    scope = scope_of(user_id)
    assert {r["branch_id"] for r in sales.branch_sales(conn, scope, group_by="total", **MAY)["rows"]} == expected
    assert {r["branch_id"] for r in waste.waste_report(conn, scope, **MAY)["rows"]} == expected
    assert {r["branch_id"] for r in staff.labor_cost_ratio(conn, scope, **MAY)["rows"]} == expected
    assert {r["branch_id"] for r in sales.compare_branches(conn, scope, "revenue", **MAY)["rows"]} == expected


def test_item_margin_shows_only_the_callers_currencies(conn):
    assert {r["currency"] for r in sales.item_margin(conn, scope_of("u_bm_smouha"), **MAY)["rows"]} == {"EGP"}
    assert {r["currency"] for r in sales.item_margin(conn, scope_of("u_bm_olaya"), **MAY)["rows"]} == {"SAR"}


def test_staff_see_only_their_own_shifts(conn):
    scope = scope_of("u_staff_olaya")
    result = staff.staff_schedule(conn, scope, branch_id="B1", on=date(2026, 5, 1), days=14)
    assert result["shifts"], "the demo staff member should have shifts in the first half of May"
    assert {s["staff_id"] for s in result["shifts"]} == {scope.staff_id}
    assert "coverage_gaps" not in result
    with pytest.raises(NotPermitted):
        staff.staff_schedule(conn, scope, branch_id="B2", on=date(2026, 5, 1))


def test_knowledge_base_filters_by_role_before_ranking(conn, kb_loaded):
    # Use a manager-only chunk's own embedding as the query: the best possible match for it.
    target = conn.execute("""SELECT doc_id, embedding::text AS v FROM kb_chunks
                             WHERE doc_id = 'reorder-policy' AND language = 'en' LIMIT 1""").fetchone()
    vector = [float(x) for x in target["v"].strip("[]").split(",")]
    manager_docs = {r["doc_id"] for r in conn.execute(
        "SELECT DISTINCT doc_id FROM kb_chunks WHERE NOT ('staff' = ANY(allowed_roles))").fetchall()}

    assert kb.search_chunks(conn, scope_of("u_bm_olaya"), vector)[0]["doc_id"] == "reorder-policy"
    staff_hits = kb.search_chunks(conn, scope_of("u_staff_olaya"), vector, limit=8)
    assert staff_hits and not {h["doc_id"] for h in staff_hits} & manager_docs


def test_pending_actions_are_routed_by_role_and_area(conn):
    with conn.transaction(force_rollback=True):
        conn.execute("""
            INSERT INTO pending_actions (action_type, branch_id, payload, amount, currency, requested_by,
                                         requested_role, approver_role)
            VALUES ('reorder', 'B1', '{}', 1200, 'SAR', 'u_bm_olaya', 'branch_manager', 'area_manager'),
                   ('reorder', 'B2', '{}', 1200, 'SAR', 'u_bm_tahlia', 'branch_manager', 'owner')""")
        area = actions.pending_actions_list(conn, scope_of("u_area_riyadh"))
        owner = actions.pending_actions_list(conn, scope_of("u_owner"))
        tahlia = actions.pending_actions_list(conn, scope_of("u_bm_tahlia"))
    assert [a["branch_id"] for a in area["waiting_for_you"]] == ["B1"]
    assert [a["branch_id"] for a in owner["waiting_for_you"]] == ["B2"]
    assert tahlia["waiting_for_you"] == [] and [a["branch_id"] for a in tahlia["requested_by_you"]] == ["B2"]
