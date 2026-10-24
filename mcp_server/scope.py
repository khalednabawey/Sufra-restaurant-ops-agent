"""Caller scope: who is asking, in which role, for which branches. Every tool starts here.

The scope arrives as a signed token (HS256 JWT) that the API issues for each user. The model
never builds or edits it, so it can't widen its own access, and every query filters rows by
the branches this module returns. Refusals say only that access is not allowed, never
whether the data exists.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

ROLES = ("owner", "area_manager", "branch_manager", "staff")
ALL_BRANCHES = ("B1", "B2", "B3", "B4")
ISSUER = "sufra-api"
DEV_SIGNING_KEY = "dev-only-signing-key-change-me-0123456789"

MANAGERS = frozenset({"owner", "area_manager", "branch_manager"})
EVERYONE = frozenset(ROLES)
TOOL_ROLES = {
    "branch_sales": MANAGERS,
    "top_items": MANAGERS,
    "item_margin": MANAGERS,
    "low_stock_items": MANAGERS,
    "waste_report": MANAGERS,
    "labor_cost_ratio": MANAGERS,
    "staff_schedule": EVERYONE,  # staff see their own shifts only
    "compare_branches": MANAGERS,
    "reviews_summary": MANAGERS,
    "search_sop": EVERYONE,  # chunks are filtered by role
    "pending_actions_list": MANAGERS,
}


class NotPermitted(Exception):
    """The caller may not see this. The message is safe to show to the user."""


@dataclass(frozen=True)
class Scope:
    user_id: str
    role: str
    branches: tuple[str, ...]
    staff_id: str | None = None

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(f"unknown role {self.role!r}")
        if not self.branches or not set(self.branches) <= set(ALL_BRANCHES):
            raise ValueError(f"invalid branch scope {self.branches!r}")
        if self.role == "staff" and not self.staff_id:
            raise ValueError("a staff scope needs a staff_id")


def signing_key() -> str:
    return os.environ.get("SCOPE_SIGNING_KEY") or DEV_SIGNING_KEY


def issue_token(scope: Scope, ttl: timedelta = timedelta(hours=8)) -> str:
    now = datetime.now(UTC)
    claims = {"iss": ISSUER, "sub": scope.user_id, "role": scope.role,
              "branches": list(scope.branches), "iat": now, "exp": now + ttl}
    if scope.staff_id:
        claims["staff_id"] = scope.staff_id
    return jwt.encode(claims, signing_key(), algorithm="HS256")


def read_token(token: str) -> Scope:
    try:
        claims = jwt.decode(token, signing_key(), algorithms=["HS256"], issuer=ISSUER,
                            options={"require": ["exp", "iss", "sub"]})
        return Scope(user_id=claims["sub"], role=claims["role"],
                     branches=tuple(claims["branches"]), staff_id=claims.get("staff_id"))
    except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
        raise NotPermitted("Your session isn't valid. Please sign in again.") from exc


def check_tool(scope: Scope, tool: str) -> None:
    if scope.role not in TOOL_ROLES[tool]:
        raise NotPermitted("Your role doesn't have access to this information.")


def branches_for(scope: Scope, requested: list[str] | None) -> list[str]:
    """The branches a query may read: the caller's whole scope, or the requested subset of it."""
    if not requested:
        return list(scope.branches)
    wanted = list(dict.fromkeys(b.strip().upper() for b in requested))
    if any(b not in scope.branches for b in wanted):
        raise NotPermitted("You don't have access to that branch.")
    return wanted


def branch_for(scope: Scope, branch_id: str) -> str:
    return branches_for(scope, [branch_id])[0]
