"""Scope and token rules. No database needed."""

import base64
import json
from datetime import timedelta

import jwt
import pytest

from mcp_server.demo_users import DEMO_USERS
from mcp_server.scope import (
    ISSUER,
    TOOL_ROLES,
    NotPermitted,
    Scope,
    branch_for,
    branches_for,
    check_tool,
    issue_token,
    read_token,
)
from tests.conftest import scope_of


@pytest.mark.parametrize("user_id", DEMO_USERS)
def test_token_round_trip(user_id):
    scope = scope_of(user_id)
    assert read_token(issue_token(scope)) == scope


def test_edited_token_is_rejected():
    """A branch manager who rewrites their token to claim every branch gets nothing."""
    header, payload, signature = issue_token(scope_of("u_bm_tahlia")).split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=="))
    claims |= {"role": "owner", "branches": ["B1", "B2", "B3", "B4"]}
    forged = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    with pytest.raises(NotPermitted):
        read_token(f"{header}.{forged}.{signature}")


def test_token_signed_with_another_key_is_rejected():
    token = jwt.encode({"iss": ISSUER, "sub": "x", "role": "owner", "branches": ["B1"], "exp": 4102444800},
                       "attacker-key-attacker-key-attacker-key", algorithm="HS256")
    with pytest.raises(NotPermitted):
        read_token(token)


def test_unsigned_token_is_rejected():
    token = jwt.encode({"iss": ISSUER, "sub": "x", "role": "owner", "branches": ["B1"], "exp": 4102444800},
                       None, algorithm="none")
    with pytest.raises(NotPermitted):
        read_token(token)


def test_expired_token_is_rejected():
    with pytest.raises(NotPermitted):
        read_token(issue_token(scope_of("u_owner"), ttl=timedelta(seconds=-1)))


@pytest.mark.parametrize("token", ["", "not-a-token", "a.b.c"])
def test_garbage_token_is_rejected(token):
    with pytest.raises(NotPermitted):
        read_token(token)


def test_default_scope_is_the_callers_branches():
    assert branches_for(scope_of("u_owner"), None) == ["B1", "B2", "B3", "B4"]
    assert branches_for(scope_of("u_area_riyadh"), []) == ["B1", "B4"]
    assert branches_for(scope_of("u_bm_tahlia"), None) == ["B2"]


@pytest.mark.parametrize("requested", [["B1"], ["B2", "B1"], ["B9"], ["all"]])
def test_branch_manager_cannot_read_other_branches(requested):
    with pytest.raises(NotPermitted):
        branches_for(scope_of("u_bm_tahlia"), requested)


def test_area_manager_is_limited_to_their_area():
    assert branches_for(scope_of("u_area_riyadh"), ["b4"]) == ["B4"]
    with pytest.raises(NotPermitted):
        branch_for(scope_of("u_area_riyadh"), "B2")


@pytest.mark.parametrize("tool", [t for t, roles in TOOL_ROLES.items() if "staff" not in roles])
def test_staff_cannot_use_manager_tools(tool):
    with pytest.raises(NotPermitted):
        check_tool(scope_of("u_staff_olaya"), tool)


@pytest.mark.parametrize("tool", ["staff_schedule", "search_sop"])
def test_staff_can_use_their_tools(tool):
    check_tool(scope_of("u_staff_olaya"), tool)


def test_refusal_does_not_reveal_data():
    with pytest.raises(NotPermitted) as exc:
        branches_for(scope_of("u_bm_tahlia"), ["B1"])
    assert "B1" not in str(exc.value)


@pytest.mark.parametrize("kwargs", [
    {"user_id": "u", "role": "admin", "branches": ("B1",)},
    {"user_id": "u", "role": "owner", "branches": ()},
    {"user_id": "u", "role": "owner", "branches": ("B9",)},
    {"user_id": "u", "role": "staff", "branches": ("B1",)},
])
def test_invalid_scopes_are_refused(kwargs):
    with pytest.raises(ValueError):
        Scope(**kwargs)
