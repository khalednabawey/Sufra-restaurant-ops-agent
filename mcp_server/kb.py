"""Load the knowledge base into kb_chunks: SOP sections from data/sop/ plus the live menu.

Each `## ` section of an SOP becomes one chunk, prefixed with the document title so it reads
on its own. The menu is generated from the price list, one chunk per category and language.

    uv run python -m mcp_server.kb ingest
"""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg
import yaml

from mcp_server.db import connect
from mcp_server.embeddings import embed, to_pgvector
from mcp_server.scope import ROLES

SOP_DIR = Path(__file__).resolve().parents[1] / "data" / "sop"
CATEGORY_NAMES = {
    "coffee": ("Coffee", "القهوة"), "tea": ("Tea", "الشاي"),
    "hot_chocolate": ("Hot chocolate and sahlab", "الشوكولاتة الساخنة والسحلب"),
    "add_ons": ("Add-ons", "الإضافات"), "bakery": ("Bakery", "المخبوزات"),
    "retail": ("Retail goods", "منتجات للبيع"),
}


def read_doc(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    _, front, body = text.split("---\n", 2)
    meta = yaml.safe_load(front)
    missing = {"doc_id", "title", "language", "roles", "source"} - set(meta)
    if missing or not set(meta["roles"]) <= set(ROLES):
        raise ValueError(f"{path}: bad front matter {meta}")
    return meta, body


def sop_chunks() -> list[dict]:
    chunks = []
    for path in sorted(SOP_DIR.rglob("*.md")):
        meta, body = read_doc(path)
        for section in body.split("\n## ")[1:]:
            heading, _, text = section.partition("\n")
            chunks.append({"doc_id": meta["doc_id"], "title": meta["title"], "language": meta["language"],
                           "allowed_roles": meta["roles"], "source": meta["source"],
                           "content": f"{meta['title']} — {heading.strip()}\n\n{text.strip()}"})
    return chunks


def menu_chunks(conn: psycopg.Connection) -> list[dict]:
    items = conn.execute("""
        SELECT m.item_id, m.name_en, m.name_ar, m.category,
               max(p.price) FILTER (WHERE p.currency = 'SAR') AS sar,
               max(p.price) FILTER (WHERE p.currency = 'EGP') AS egp
        FROM menu_items m JOIN item_prices p USING (item_id)
        WHERE m.is_active AND p.valid_from = (SELECT max(valid_from) FROM item_prices x
                                              WHERE x.item_id = p.item_id AND x.currency = p.currency)
        GROUP BY 1, 2, 3, 4 ORDER BY m.category, m.name_en""").fetchall()
    chunks = []
    for category, (en, ar) in CATEGORY_NAMES.items():
        rows = [r for r in items if r["category"] == category]
        lines_en = [f"- {r['name_en']}: SAR {r['sar']:g} (Saudi branches), EGP {r['egp']:g} (Smouha)" for r in rows]
        lines_ar = [f"- {r['name_ar']}: {r['sar']:g} ر.س (فروع السعودية)، {r['egp']:g} ج.م (سموحة)" for r in rows]
        for lang, title, lines in (("en", f"Menu — {en}", lines_en), ("ar", f"قائمة المنيو — {ar}", lines_ar)):
            chunks.append({"doc_id": f"menu-{category}", "title": title, "language": lang,
                           "allowed_roles": list(ROLES), "source": "menu",
                           "content": f"{title}\n\n" + "\n".join(lines)})
    return chunks


def ingest(conn: psycopg.Connection) -> int:
    chunks = sop_chunks() + menu_chunks(conn)
    vectors = embed([c["content"] for c in chunks])
    with conn.transaction():
        conn.execute("TRUNCATE kb_chunks RESTART IDENTITY")
        with conn.cursor() as cur:
            cur.executemany("""
                INSERT INTO kb_chunks (doc_id, title, language, allowed_roles, source, content, embedding)
                VALUES (%(doc_id)s, %(title)s, %(language)s, %(allowed_roles)s, %(source)s, %(content)s,
                        %(embedding)s::vector)""",
                [c | {"embedding": to_pgvector(v)} for c, v in zip(chunks, vectors, strict=True)])
    return len(chunks)


def main() -> None:
    if sys.argv[1:] != ["ingest"]:
        raise SystemExit("usage: python -m mcp_server.kb ingest")
    with connect() as conn:
        print(f"kb_chunks: {ingest(conn)} chunks embedded")


if __name__ == "__main__":
    main()
