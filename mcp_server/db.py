"""Database access shared by the tools."""

from __future__ import annotations

import os
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

load_dotenv()

EGP_PER_SAR = 13.0  # fixed reporting rate for comparing branches across currencies


def database_url() -> str:
    return os.environ.get("DATABASE_URL", "postgresql://sufra:sufra@localhost:5433/sufra")


def connect() -> psycopg.Connection:
    return psycopg.connect(database_url(), row_factory=dict_row, autocommit=True)


def rows(conn: psycopg.Connection, sql: str, params: dict | None = None) -> list[dict[str, Any]]:
    return [clean(r) for r in conn.execute(sql, params or {}).fetchall()]


def clean(row: dict[str, Any]) -> dict[str, Any]:
    """Make a row JSON-friendly: numbers as floats, dates as ISO strings."""
    out = {}
    for key, value in row.items():
        if isinstance(value, Decimal):
            value = float(value)
        elif isinstance(value, datetime):
            value = value.isoformat(sep=" ", timespec="minutes")
        elif isinstance(value, date):
            value = value.isoformat()
        out[key] = value
    return out


def check_range(date_from: date, date_to: date) -> None:
    if date_from > date_to:
        raise ValueError("date_from must be on or before date_to")


def data_range(conn: psycopg.Connection) -> tuple[date, date]:
    r = conn.execute("SELECT min(sold_on) AS f, max(sold_on) AS t FROM sales").fetchone()
    return r["f"], r["t"]


def period_notes(conn: psycopg.Connection, branches: list[str], date_from: date, date_to: date) -> list[str]:
    """Warnings the answer must carry: dates outside the data, branches open for part of the period."""
    notes = []
    first, last = data_range(conn)
    if date_from < first or date_to > last:
        notes.append(f"Data covers {first} to {last}; dates outside that range have no data.")
    for b in conn.execute("SELECT branch_id, name_en, opened_on FROM branches WHERE branch_id = ANY(%s) "
                          "AND opened_on > %s ORDER BY 1", (branches, date_from)).fetchall():
        notes.append(f"{b['branch_id']} {b['name_en']} opened on {b['opened_on']}, so it covers only part "
                     f"of this period. Compare it per day, not in totals.")
    return notes


def summary(branches: list[str], date_from: date, date_to: date, what: str) -> str:
    return f"{', '.join(branches)} · {date_from} to {date_to} · {what}"
