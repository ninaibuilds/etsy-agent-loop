import os
import sqlite3
from pathlib import Path

_DATA_DIR = Path(os.getenv("DATA_DIR", "."))
DB_PATH   = _DATA_DIR / "etsy_agent.db"


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    with _conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS listings (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                listing_id   TEXT    UNIQUE NOT NULL,
                url          TEXT    NOT NULL,
                printify_id  TEXT,
                title        TEXT,
                niche        TEXT,
                created_at   TEXT    DEFAULT CURRENT_TIMESTAMP
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS scrape_runs (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                loop_count          INTEGER,
                products_found      INTEGER,
                designs_created     INTEGER,
                listings_published  INTEGER,
                run_at              TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        con.commit()


def is_duplicate(listing_id: str) -> bool:
    with _conn() as con:
        row = con.execute(
            "SELECT 1 FROM listings WHERE listing_id = ?", (listing_id,)
        ).fetchone()
        return row is not None


def track_listing(
    listing_id: str,
    url: str,
    printify_id: str = "",
    title: str = "",
    niche: str = "",
) -> None:
    with _conn() as con:
        con.execute(
            """INSERT OR IGNORE INTO listings
               (listing_id, url, printify_id, title, niche)
               VALUES (?, ?, ?, ?, ?)""",
            (listing_id, url, printify_id, title, niche),
        )
        con.commit()


def log_run(
    loop_count: int,
    products_found: int,
    designs_created: int,
    listings_published: int,
) -> None:
    with _conn() as con:
        con.execute(
            """INSERT INTO scrape_runs
               (loop_count, products_found, designs_created, listings_published)
               VALUES (?, ?, ?, ?)""",
            (loop_count, products_found, designs_created, listings_published),
        )
        con.commit()


def get_all_listing_urls() -> list[str]:
    with _conn() as con:
        rows = con.execute(
            "SELECT url FROM listings ORDER BY created_at DESC"
        ).fetchall()
        return [r["url"] for r in rows]


# Initialise on first import
init_db()
