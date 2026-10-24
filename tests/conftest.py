import os

import psycopg
import pytest

from mcp_server.db import connect
from mcp_server.demo_users import DEMO_USERS


def _skip_or_fail(reason: str):
    # CI sets REQUIRE_DB=1 so a missing database fails instead of silently skipping.
    if os.environ.get("REQUIRE_DB"):
        pytest.fail(reason)
    pytest.skip(reason)


@pytest.fixture(scope="session")
def conn():
    try:
        c = connect()
    except psycopg.OperationalError:
        _skip_or_fail("database not reachable: run `make db-up && make seed`")
    if c.execute("SELECT to_regclass('sales') IS NULL AS missing").fetchone()["missing"] or \
            not c.execute("SELECT EXISTS (SELECT 1 FROM sales) AS loaded").fetchone()["loaded"]:
        _skip_or_fail("database not seeded: run `make data && make seed`")
    yield c
    c.close()


@pytest.fixture(scope="session")
def kb_loaded(conn):
    if not conn.execute("SELECT EXISTS (SELECT 1 FROM kb_chunks) AS loaded").fetchone()["loaded"]:
        _skip_or_fail("knowledge base empty: run `python -m mcp_server.kb ingest`")


def scope_of(user_id: str):
    return DEMO_USERS[user_id][1]
