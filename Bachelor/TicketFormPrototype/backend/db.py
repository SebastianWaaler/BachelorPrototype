import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[1] / "tickets.db"
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Better concurrency for small web apps:
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def init_db():
    conn = get_conn()
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()
