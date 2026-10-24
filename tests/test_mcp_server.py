"""The MCP server end to end, through an in-process MCP client. Needs the seeded database."""

import asyncio

import jwt
import pytest
from mcp import Client

from mcp_server.scope import ISSUER, issue_token
from mcp_server.server import mcp
from tests.conftest import scope_of

READ_TOOLS = {"branch_sales", "top_items", "item_margin", "low_stock_items", "waste_report", "labor_cost_ratio",
              "staff_schedule", "compare_branches", "reviews_summary", "search_sop", "pending_actions_list"}
MAY = {"date_from": "2026-05-01", "date_to": "2026-05-31"}


def call(name: str, args: dict):
    async def run():
        async with Client(mcp) as client:
            return await client.call_tool(name, args)
    return asyncio.run(run())


def text(result) -> str:
    return " ".join(c.text for c in result.content if hasattr(c, "text"))


def test_server_lists_the_eleven_read_tools():
    async def run():
        async with Client(mcp) as client:
            return await client.list_tools()
    tools = asyncio.run(run()).tools
    assert {t.name for t in tools} == READ_TOOLS
    assert all("caller_token" in t.input_schema["required"] for t in tools)


def test_owner_gets_data(conn):
    result = call("branch_sales", {"caller_token": issue_token(scope_of("u_owner")), "group_by": "total"} | MAY)
    assert not result.is_error
    assert {r["branch_id"] for r in result.structured_content["rows"]} == {"B1", "B2", "B3", "B4"}


@pytest.mark.parametrize("args", [
    {"branch_ids": ["B1"]},
    {"branch_ids": ["B2", "B1"]},
    {"branch_ids": ["B1", "B2", "B3", "B4"]},
])
def test_branch_manager_asking_for_other_branches_is_refused(conn, args):
    token = issue_token(scope_of("u_bm_tahlia"))
    result = call("branch_sales", {"caller_token": token} | MAY | args)
    assert result.is_error and "not_permitted:" in text(result)
    assert "revenue" not in text(result)


def test_forged_owner_token_is_refused(conn):
    forged = jwt.encode({"iss": ISSUER, "sub": "u_owner", "role": "owner", "branches": ["B1", "B2", "B3", "B4"],
                         "exp": 4102444800}, "guessed-key-guessed-key-guessed-key", algorithm="HS256")
    result = call("compare_branches", {"caller_token": forged, "metric": "revenue"} | MAY)
    assert result.is_error and "not_permitted:" in text(result)


def test_staff_token_cannot_read_sales(conn):
    result = call("branch_sales", {"caller_token": issue_token(scope_of("u_staff_olaya"))} | MAY)
    assert result.is_error and "not_permitted:" in text(result)


def test_bad_arguments_are_reported_not_crashed(conn):
    token = issue_token(scope_of("u_owner"))
    result = call("branch_sales", {"caller_token": token, "date_from": "2026-05-31", "date_to": "2026-05-01"})
    assert result.is_error and "invalid_request:" in text(result)
