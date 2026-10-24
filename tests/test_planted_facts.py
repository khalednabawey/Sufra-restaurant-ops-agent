"""The planted facts hold in the database (data/checks.sql) and the tools surface them."""

import re
from datetime import date
from pathlib import Path

import pytest
from psycopg.rows import tuple_row

from mcp_server.tools import inventory, sales, staff, waste
from tests.conftest import scope_of

CHECKS = [s.strip() for s in (Path(__file__).parents[1] / "data" / "checks.sql").read_text(encoding="utf-8")
          .split(";\n") if s.strip() and not all(line.startswith("--") for line in s.strip().splitlines())]
RAMADAN = {"date_from": date(2026, 2, 18), "date_to": date(2026, 3, 19)}


@pytest.mark.parametrize("sql", CHECKS, ids=lambda s: re.search(r"SELECT '(\w+)'", s).group(1))
def test_sql_check(conn, sql):
    with conn.cursor(row_factory=tuple_row) as cur:
        for name, passed, detail in cur.execute(sql).fetchall():
            assert passed is True, f"{name} failed: {detail}"


def test_low_stock_tool_finds_the_trap(conn):
    result = inventory.low_stock_items(conn, scope_of("u_bm_olaya"), branch_id="B1")
    assert [r["ingredient_id"] for r in result["rows"]] == ["espresso_beans"]
    assert result["as_of"] == "2026-06-30"


def test_margin_tool_puts_the_loss_maker_first(conn):
    worst = sales.item_margin(conn, scope_of("u_owner"), date(2026, 6, 1), date(2026, 6, 30), limit=3)["rows"]
    assert {(r["item_id"], r["margin"] < 0) for r in worst[:2]} == {("pistachio_maamoul", True)}
    assert worst[2]["margin"] > 0
    one = sales.item_margin(conn, scope_of("u_bm_olaya"), date(2026, 3, 1), date(2026, 4, 30), item="pistachio")
    assert [t["cost_from"] for t in one["rows"][0]["cost_trend"]] == ["2026-03-01", "2026-04-01"]


def test_waste_tool_shows_the_tahlia_spike(conn):
    owner = scope_of("u_owner")
    may = waste.waste_report(conn, owner, date(2026, 5, 1), date(2026, 5, 31), group_by="reason")["rows"]
    share = {r["branch_id"]: r["waste_share"] for r in may}
    assert max(share, key=share.get) == "B2" and share["B2"] > 2 * max(v for b, v in share.items() if b != "B2")
    b2 = next(r for r in may if r["branch_id"] == "B2")
    assert b2["breakdown"][0]["reason"] == "prep_error"


def test_labour_tool_shows_olaya_ramadan_overtime(conn):
    rows = {r["branch_id"]: r for r in staff.labor_cost_ratio(conn, scope_of("u_owner"), **RAMADAN)["rows"]}
    assert rows["B1"]["labour_ratio"] > 0.30 and rows["B1"]["overtime_hours"] > 0
    assert all(rows[b]["labour_ratio"] <= 0.30 and rows[b]["overtime_hours"] == 0 for b in ("B2", "B3"))


def test_compare_flags_the_new_branch(conn):
    result = sales.compare_branches(conn, scope_of("u_owner"), "revenue", date(2026, 1, 1), date(2026, 6, 30))
    partial = {r["branch_id"]: r["partial_period"] for r in result["rows"]}
    assert partial == {"B1": False, "B2": False, "B3": False, "B4": True}
    assert any("B4 Al Malqa opened on 2026-04-01" in n for n in result["notes"])
