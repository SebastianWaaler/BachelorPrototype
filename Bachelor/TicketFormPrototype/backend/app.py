from flask import Flask, request, jsonify
from flask_cors import CORS
import time
import os
import sqlite3

app = Flask(__name__)
CORS(app)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "..", "tickets.db")

def now_ms() -> int:
    return int(time.time() * 1000)

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn

def init_db():
    conn = get_conn()
    conn.executescript("""
    PRAGMA foreign_keys = ON;

    CREATE TABLE IF NOT EXISTS ticket_drafts (
      user_id INTEGER PRIMARY KEY,         -- user number: 1..99
      started_at INTEGER NOT NULL,          -- unix ms
      last_activity_at INTEGER NOT NULL,
      ai_turns INTEGER DEFAULT 0,
      state TEXT NOT NULL CHECK (state IN ('draft','submitted','abandoned'))
    );

    CREATE TABLE IF NOT EXISTS tickets (
      user_id INTEGER NOT NULL,             -- user number: 1..99
      title TEXT NOT NULL,
      description TEXT NOT NULL,
      created_at INTEGER NOT NULL,          -- unix ms
      time_to_submit_ms INTEGER,
      ai_used INTEGER DEFAULT 0,
      status TEXT DEFAULT 'open'
    );
    """)
    conn.commit()
    conn.close()

# -----------------------------
# Routes
# -----------------------------

@app.get("/api/ping")
def ping():
    return jsonify({"ok": True})

@app.post("/api/draft/start")
def start_draft():
    data = request.get_json(force=True)
    user_id = data.get("user_id")

    if not isinstance(user_id, int) or not (1 <= user_id <= 99):
        return jsonify({"error": "user_id must be an integer between 1 and 99"}), 400

    conn = get_conn()
    t = now_ms()

    # One active draft per user. Re-confirm overwrites and restarts timer.
    conn.execute(
        """
        INSERT INTO ticket_drafts (user_id, started_at, last_activity_at, state)
        VALUES (?, ?, ?, 'draft')
        ON CONFLICT(user_id) DO UPDATE SET
            started_at = excluded.started_at,
            last_activity_at = excluded.last_activity_at,
            state = 'draft'
        """,
        (user_id, t, t)
    )

    conn.commit()
    conn.close()

    return jsonify({"user_id": user_id, "started_at": t})

@app.post("/api/tickets")
def create_ticket():
    data = request.get_json(force=True)

    user_id = data.get("user_id")
    title = (data.get("title") or "").strip()
    description = (data.get("description") or "").strip()

    if not isinstance(user_id, int) or not (1 <= user_id <= 99):
        return jsonify({"error": "user_id must be an integer between 1 and 99"}), 400
    if not title or not description:
        return jsonify({"error": "title and description required"}), 400

    conn = get_conn()

    draft = conn.execute(
        "SELECT * FROM ticket_drafts WHERE user_id = ? AND state = 'draft'",
        (user_id,)
    ).fetchone()

    if not draft:
        conn.close()
        return jsonify({"error": "No active draft for this user. Click Confirm first."}), 400

    created_at = now_ms()
    time_spent = created_at - draft["started_at"]

    conn.execute(
        """
        INSERT INTO tickets (user_id, title, description, created_at, time_to_submit_ms, ai_used, status)
        VALUES (?, ?, ?, ?, ?, 0, 'open')
        """,
        (user_id, title, description, created_at, time_spent)
    )

    conn.execute(
        "UPDATE ticket_drafts SET state='submitted', last_activity_at=? WHERE user_id=?",
        (created_at, user_id)
    )

    conn.commit()
    conn.close()

    return jsonify({"user_id": user_id, "time_to_submit_ms": time_spent}), 201

@app.get("/api/tickets")
def list_tickets():
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT user_id, title, created_at, time_to_submit_ms, status
        FROM tickets
        ORDER BY created_at DESC
        LIMIT 100
        """
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

if __name__ == "__main__":
    print("Starting backend on http://127.0.0.1:5000")
    init_db()
    app.run(host="127.0.0.1", port=5000, debug=True)
