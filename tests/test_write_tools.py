"""Write tools only create pending requests, routed per the SOPs; only an approver's decision changes
a status. Needs the seeded database. Every test cleans up the rows it creates."""

import pytest

from mcp_server.scope import NotPermitted, check_tool
from mcp_server.tools import actions
from tests.conftest import scope_of


@pytest.fixture
def created(conn):
    ids: list[int] = []
    yield ids
    if ids:
        conn.execute("DELETE FROM pending_actions WHERE action_id = ANY(%s)", (ids,))


def reorder(conn, created, user, **kw):
    out = actions.request_reorder(conn, scope_of(user), **kw)
    created.append(out["action"]["action_id"])
    return out["action"]


def test_branch_manager_reorder_goes_to_area_manager(conn, created):
    a = reorder(conn, created, "u_bm_olaya", branch_id="B1", ingredient="milk", qty=40, unit="l")
    assert a["status"] == "pending" and a["approver_role"] == "area_manager"
    assert a["branch_id"] == "B1" and a["currency"] == "SAR" and a["amount"] > 0
    assert a["payload"]["qty_base"] == 40_000


def test_jeddah_reorder_goes_to_owner(conn, created):
    a = reorder(conn, created, "u_bm_tahlia", branch_id="B2", ingredient="milk", qty=10, unit="l")
    assert a["approver_role"] == "owner"


def test_area_manager_request_goes_to_owner(conn, created):
    a = reorder(conn, created, "u_area_riyadh", branch_id="B4", ingredient="milk", qty=5, unit="l")
    assert a["approver_role"] == "owner"


def test_reorder_for_another_branch_is_refused(conn):
    with pytest.raises(NotPermitted):
        actions.request_reorder(conn, scope_of("u_bm_tahlia"), branch_id="B1", ingredient="milk", qty=1, unit="l")


def test_wrong_unit_is_rejected(conn):
    with pytest.raises(ValueError, match="counted in ml"):
        actions.request_reorder(conn, scope_of("u_bm_olaya"), branch_id="B1", ingredient="milk", qty=1, unit="kg")


def test_staff_and_owner_cannot_request_reorders():
    for user in ("u_staff_olaya", "u_owner"):
        with pytest.raises(NotPermitted):
            check_tool(scope_of(user), "request_reorder")


def test_duplicate_reorder_is_not_created_twice(conn, created):
    first = reorder(conn, created, "u_bm_olaya", branch_id="B1", ingredient="milk", qty=7, unit="l")
    again = actions.request_reorder(conn, scope_of("u_bm_olaya"), branch_id="B1", ingredient="milk", qty=7, unit="l")
    assert again["created"] is False and again["action"]["action_id"] == first["action_id"]


def test_price_change_needs_a_reason_and_goes_to_owner(conn, created):
    with pytest.raises(ValueError, match="reason"):
        actions.propose_price_change(conn, scope_of("u_area_riyadh"), item="latte_rg", new_price=19, reason="")
    out = actions.propose_price_change(conn, scope_of("u_area_riyadh"), item="latte_rg", new_price=19,
                                       reason="milk cost went up")
    created.append(out["action"]["action_id"])
    assert out["action"]["approver_role"] == "owner" and out["action"]["payload"]["currency"] == "SAR"


def test_area_manager_cannot_change_egp_prices(conn):
    with pytest.raises(NotPermitted):
        actions.propose_price_change(conn, scope_of("u_area_riyadh"), item="latte_rg", new_price=200,
                                     reason="test change", currency="EGP")


def test_only_the_right_approver_decides(conn, created):
    a = reorder(conn, created, "u_bm_olaya", branch_id="B1", ingredient="milk", qty=3, unit="l")
    for user in ("u_bm_olaya", "u_bm_tahlia", "u_staff_olaya", "u_owner"):  # owner isn't the routed approver
        with pytest.raises(NotPermitted):
            actions.decide_action(conn, scope_of(user), a["action_id"], "approved")
    done = actions.decide_action(conn, scope_of("u_area_riyadh"), a["action_id"], "approved", note="ok")
    assert done["action"]["status"] == "approved" and done["action"]["decided_by"] == "u_area_riyadh"
    with pytest.raises(ValueError, match="already approved"):
        actions.decide_action(conn, scope_of("u_area_riyadh"), a["action_id"], "rejected")


def test_nobody_approves_their_own_reorder(conn, created):
    a = reorder(conn, created, "u_area_riyadh", branch_id="B1", ingredient="milk", qty=2, unit="l")
    with pytest.raises(NotPermitted):
        actions.decide_action(conn, scope_of("u_area_riyadh"), a["action_id"], "approved")
    assert actions.decide_action(conn, scope_of("u_owner"), a["action_id"], "rejected")["action"]["status"] == \
        "rejected"


def test_unknown_action_gives_the_same_refusal(conn):
    with pytest.raises(NotPermitted, match="can't decide"):
        actions.decide_action(conn, scope_of("u_owner"), 999_999_999, "approved")
