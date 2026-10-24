"""Load data/build/*.csv into PostgreSQL.

Re-creates every table from data/schema.sql first, so the database always matches the
schema and the latest build. Run `python -m mcp_server.kb ingest` afterwards to load the
knowledge base.

    uv run python data/seed.py
"""

import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

DATA = Path(__file__).parent
BUILD = DATA / "build"
TABLES = [  # parents before children
    "branches", "calendar", "weather_daily", "menu_items", "source_product_map", "item_prices",
    "suppliers", "ingredients", "ingredient_costs", "recipes", "sales", "inventory_daily",
    "waste_events", "staff", "shifts", "reviews",
]
SERIALS = {"waste_events": "waste_id", "shifts": "shift_id", "reviews": "review_id"}


def main() -> None:
    load_dotenv()
    url = os.environ.get("DATABASE_URL", "postgresql://sufra:sufra@localhost:5433/sufra")
    with psycopg.connect(url) as conn:
        conn.execute((DATA / "schema.sql").read_text(encoding="utf-8"))
        for table in TABLES:
            path = BUILD / f"{table}.csv"
            with path.open(encoding="utf-8") as f:
                columns = f.readline().strip()
            with conn.cursor() as cur:
                with cur.copy(f"COPY {table} ({columns}) FROM STDIN WITH (FORMAT csv, HEADER true)") as copy:
                    copy.write(path.read_bytes())
                if table in SERIALS:
                    col = SERIALS[table]
                    cur.execute(f"SELECT setval(pg_get_serial_sequence('{table}', '{col}'), max({col})) "
                                f"FROM {table}")
                cur.execute(f"SELECT count(*) FROM {table}")
                print(f"{table:<20} {cur.fetchone()[0]:>9,}")
        conn.execute("ANALYZE")


if __name__ == "__main__":
    main()
