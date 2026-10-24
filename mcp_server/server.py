"""MCP server with Sufra's read tools.

Every tool takes `caller_token`, a signed token that the application fills in for the signed-in
user (the model never writes it). The server verifies it, checks the role may use the tool, and
the tool filters every query by the caller's branches. A refusal comes back as an error result
starting with `not_permitted:`.

    uv run python -m mcp_server.server                                    # stdio
    uv run python -m mcp_server.server --transport streamable-http --port 8001
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from datetime import date
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from mcp_server.db import connect
from mcp_server.scope import NotPermitted, check_tool, read_token
from mcp_server.tools import actions, inventory, kb, reviews, sales, staff, waste

mcp = MCPServer(
    "sufra-ops",
    instructions=(
        "Read-only tools for Sufra Café & Bakery (4 branches: B1 Olaya and B4 Al Malqa in Riyadh, "
        "B2 Tahlia in Jeddah, B3 Smouha in Alexandria). Data covers 2026-01-01 to 2026-06-30. "
        "Every result carries a query_summary and notes; quote numbers only from results."
    ),
)

Token = Annotated[str, Field(description="Signed session token for the caller. Filled in by the application; "
                                         "never invent or change it.")]
DateFrom = Annotated[date, Field(description="First day, YYYY-MM-DD. Data covers 2026-01-01 to 2026-06-30.")]
DateTo = Annotated[date, Field(description="Last day, inclusive, YYYY-MM-DD.")]
Branches = Annotated[list[str] | None, Field(description="Branch ids, e.g. ['B2']. Omit for every branch "
                                                         "the caller may see.")]
BranchId = Annotated[str, Field(description="One branch id: B1 Olaya, B2 Tahlia, B3 Smouha or B4 Al Malqa.")]


def _call(tool: str, token: str, fn: Callable[..., dict], **kwargs: Any) -> dict[str, Any]:
    try:
        scope = read_token(token)
        check_tool(scope, tool)
        with connect() as conn:
            return fn(conn, scope, **kwargs)
    except NotPermitted as exc:
        raise ToolError(f"not_permitted: {exc}") from exc
    except ValueError as exc:
        raise ToolError(f"invalid_request: {exc}") from exc


@mcp.tool()
def branch_sales(caller_token: Token, date_from: DateFrom, date_to: DateTo, branch_ids: Branches = None,
                 group_by: Literal["hour", "day", "week", "month", "category", "total"] = "day") -> dict[str, Any]:
    """Revenue, order count and average ticket per branch, grouped by hour of day, day, week, month,
    menu category or one total. Amounts are in each branch's currency (SAR, or EGP for B3)."""
    return _call("branch_sales", caller_token, sales.branch_sales, date_from=date_from, date_to=date_to,
                 branch_ids=branch_ids, group_by=group_by)


@mcp.tool()
def top_items(caller_token: Token, date_from: DateFrom, date_to: DateTo, branch_ids: Branches = None,
              metric: Literal["qty", "revenue", "margin"] = "revenue",
              limit: Annotated[int, Field(ge=1, le=50)] = 10) -> dict[str, Any]:
    """Best-selling menu items ranked by quantity, revenue or margin (revenue − recipe cost), per currency."""
    return _call("top_items", caller_token, sales.top_items, date_from=date_from, date_to=date_to,
                 branch_ids=branch_ids, metric=metric, limit=limit)


@mcp.tool()
def item_margin(caller_token: Token, date_from: DateFrom, date_to: DateTo,
                item: Annotated[str, Field(description="A menu item id or part of its English or Arabic name "
                                                       "(e.g. 'latte', 'معمول'), or 'all'.")] = "all",
                limit: Annotated[int, Field(ge=1, le=200, description="For 'all': how many of the "
                                                                      "lowest-margin items to return.")] = 20,
                ) -> dict[str, Any]:
    """Menu price, recipe cost and margin on date_to. For one item, also how its cost changed over the
    period. For 'all', the lowest-margin items first, so a negative margin shows at the top."""
    return _call("item_margin", caller_token, sales.item_margin, date_from=date_from, date_to=date_to,
                 item=item, limit=limit)


@mcp.tool()
def low_stock_items(caller_token: Token, branch_id: BranchId,
                    days_of_cover_threshold: Annotated[float, Field(gt=0, le=60)] = 2.0) -> dict[str, Any]:
    """Ingredients and goods at one branch whose latest stock covers fewer days than the threshold
    (days of cover = closing stock ÷ average daily use over the last 14 days)."""
    return _call("low_stock_items", caller_token, inventory.low_stock_items, branch_id=branch_id,
                 days_of_cover_threshold=days_of_cover_threshold)


@mcp.tool()
def waste_report(caller_token: Token, date_from: DateFrom, date_to: DateTo, branch_ids: Branches = None,
                 group_by: Literal["ingredient", "reason", "week"] = "ingredient") -> dict[str, Any]:
    """Waste cost per branch and its share of stock used, broken down by ingredient, reason
    (expired, prep_error, damaged, quality) or week."""
    return _call("waste_report", caller_token, waste.waste_report, date_from=date_from, date_to=date_to,
                 branch_ids=branch_ids, group_by=group_by)


@mcp.tool()
def labor_cost_ratio(caller_token: Token, date_from: DateFrom, date_to: DateTo, branch_ids: Branches = None,
                     group_by: Literal["total", "month", "week"] = "total") -> dict[str, Any]:
    """Labour cost ÷ sales per branch, with hours and overtime hours."""
    return _call("labor_cost_ratio", caller_token, staff.labor_cost_ratio, date_from=date_from,
                 date_to=date_to, branch_ids=branch_ids, group_by=group_by)


@mcp.tool()
def staff_schedule(caller_token: Token, branch_id: BranchId,
                   date: Annotated[date, Field(description="First day, YYYY-MM-DD.")],
                   days: Annotated[int, Field(ge=1, le=14)] = 1) -> dict[str, Any]:
    """Shifts at one branch for 1–14 days and the hours short of staff. Staff see only their own shifts."""
    return _call("staff_schedule", caller_token, staff.staff_schedule, branch_id=branch_id, on=date, days=days)


@mcp.tool()
def compare_branches(caller_token: Token,
                     metric: Literal["revenue", "orders", "avg_ticket", "labor_cost_ratio", "waste_share"],
                     date_from: DateFrom, date_to: DateTo) -> dict[str, Any]:
    """Rank the caller's branches on one metric. Revenue is converted to SAR and compared per trading
    day; branches open for only part of the period are flagged."""
    return _call("compare_branches", caller_token, sales.compare_branches, metric=metric,
                 date_from=date_from, date_to=date_to)


@mcp.tool()
def reviews_summary(caller_token: Token, branch_id: BranchId, date_from: DateFrom,
                    date_to: DateTo) -> dict[str, Any]:
    """Customer review count, positive share and a few sample quotes (Arabic) for one branch."""
    return _call("reviews_summary", caller_token, reviews.reviews_summary, branch_id=branch_id,
                 date_from=date_from, date_to=date_to)


@mcp.tool()
def search_sop(caller_token: Token,
               query: Annotated[str, Field(description="The question, in Arabic or English.")],
               language: Annotated[Literal["ar", "en"] | None,
                                   Field(description="Only chunks in this language; omit to search both.")] = None,
               limit: Annotated[int, Field(ge=1, le=8)] = 4) -> dict[str, Any]:
    """Search company SOPs (reorders, approvals, prices, discounts, waste, scheduling, complaints,
    checklists, food safety), FDA Food Code extracts and the menu. Only chunks the caller's role may
    read are searched."""
    return _call("search_sop", caller_token, kb.search_sop, query=query, language=language, limit=limit)


@mcp.tool()
def pending_actions_list(caller_token: Token,
                         status: Literal["pending", "approved", "rejected", "all"] = "pending") -> dict[str, Any]:
    """Reorders and price changes waiting for the caller's approval, and the ones the caller requested."""
    return _call("pending_actions_list", caller_token, actions.pending_actions_list, status=status)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sufra MCP server")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    if args.transport == "stdio":
        mcp.run("stdio")
    else:
        mcp.run("streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
