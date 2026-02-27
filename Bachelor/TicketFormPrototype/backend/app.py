"""
backend.py

Flask backend for a simple IT helpdesk ticket app.

What this service does (high level)
-----------------------------------
1) Tracks a user's "ticket draft" (when they started, what they typed, AI Q/A state).
2) Lets a user submit a ticket normally (title + description) OR use AI:
   - AI can ask follow-up questions if the initial description is too vague.
   - AI can then produce an improved, clearer final description.
3) Stores everything in a local SQLite database (tickets.db).

Key concepts in the DB
----------------------
- ticket_drafts: one active draft per user_id (user_id is the primary key).
  This table supports the "Confirm first" flow: user starts a draft, then submits/uses AI.
- tickets: the final submitted tickets (whether AI was used or not).

Security / privacy notes
------------------------
- The AI prompts explicitly avoid asking for passwords or secrets.
- This server accepts CORS requests (useful for a separate frontend).
"""

# -----------------------------
# Imports
# -----------------------------
from flask import Flask, request, jsonify
from flask_cors import CORS

import time
import os
import sqlite3
import json

from openai import OpenAI
from dotenv import load_dotenv
from pathlib import Path

# -----------------------------
# Environment / configuration
# -----------------------------

# Force loading .env from the same folder as this backend file.
# `override=True` means values in that .env will replace any already-set env vars.
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path, override=True)

# Create the Flask app and allow cross-origin requests.
# CORS is needed because the frontend runs on a different port/domain.
app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

# Absolute path to this backend folder.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Database path: one level up from backend folder, named tickets.db.
# Example structure:
#   project/
#     tickets.db
#     backend/
#       backend.py  (this file)
DB_PATH = os.path.join(BASE_DIR, "..", "tickets.db")

# OpenAI client picks up OPENAI_API_KEY from environment variables / .env file.
client = OpenAI()

def now_s() -> int: # Defines the timer 
    return int(time.time())

def get_conn(): # Establishes connection to the database, enabling foreign keys and journal mode (multiple entries at once)
    conn = sqlite3.connect(
        DB_PATH,
        timeout=10,
        isolation_level=None,
        check_same_thread=False
        )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 10000;")
    return conn

def init_db(): #Creates the tables
    conn = get_conn()

    # Create the main tables if they don't exist.
    # ticket_drafts has one row per user_id (PRIMARY KEY).
    # tickets stores submitted tickets and metadata.
    conn.executescript("""
    PRAGMA foreign_keys = ON;

    CREATE TABLE IF NOT EXISTS ticket_drafts (
      user_id INTEGER PRIMARY KEY,
      started_at INTEGER NOT NULL,
      user_id INTEGER PRIMARY KEY,
      started_at INTEGER NOT NULL,
      last_activity_at INTEGER NOT NULL,
      ai_turns INTEGER DEFAULT 0,
                        state TEXT NOT NULL CHECK (state IN ('draft','submitted','abandoned')),
                        started_at INTEGER,
                        submitted_at INTEGER,
                        log_table INTEGER
    );

        -- Create five separate ticket tables (users choose one when starting a draft)
        CREATE TABLE IF NOT EXISTS tickets_1 (
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            time_to_submit_ms INTEGER,
            ai_used INTEGER DEFAULT 0,
            status TEXT DEFAULT 'open'
        );
        CREATE TABLE IF NOT EXISTS tickets_2 (
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            time_to_submit_ms INTEGER,
            ai_used INTEGER DEFAULT 0,
            status TEXT DEFAULT 'open'
        );
        CREATE TABLE IF NOT EXISTS tickets_3 (
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            time_to_submit_ms INTEGER,
            ai_used INTEGER DEFAULT 0,
            status TEXT DEFAULT 'open'
        );
        CREATE TABLE IF NOT EXISTS tickets_4 (
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            time_to_submit_ms INTEGER,
            ai_used INTEGER DEFAULT 0,
            status TEXT DEFAULT 'open'
        );
        CREATE TABLE IF NOT EXISTS tickets_5 (
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            time_to_submit_ms INTEGER,
            ai_used INTEGER DEFAULT 0,
            status TEXT DEFAULT 'open'
        );
    """)
    conn.commit()

    # Read the existing columns in ticket_drafts so we can add missing ones.
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(ticket_drafts)").fetchall()}

    def add_col(sql):
        """
        Helper to add a column. If it already exists, SQLite raises OperationalError.
        We ignore that to make the operation idempotent.
        """
        try:
            conn.execute(sql)
            conn.commit()
        except sqlite3.OperationalError:
            pass

    # Columns added over time (migration style)
    if "draft_title" not in cols:
        add_col("ALTER TABLE ticket_drafts ADD COLUMN draft_title TEXT;")
    if "draft_description" not in cols:
        add_col("ALTER TABLE ticket_drafts ADD COLUMN draft_description TEXT;")
    if "ai_questions_json" not in cols:
        add_col("ALTER TABLE ticket_drafts ADD COLUMN ai_questions_json TEXT;")
    if "ai_answers_json" not in cols:
        add_col("ALTER TABLE ticket_drafts ADD COLUMN ai_answers_json TEXT;")

    if "started_at" not in cols:
        add_col("ALTER TABLE ticket_drafts ADD COLUMN started_at INTEGER;")
    if "submitted_at" not in cols:
        add_col("ALTER TABLE ticket_drafts ADD COLUMN submitted_at INTEGER;")
    if "log_table" not in cols:
        add_col("ALTER TABLE ticket_drafts ADD COLUMN log_table INTEGER;")

    conn.close()

def should_ask_followups(description: str) -> bool:
    """
    Quick heuristic to decide whether the AI should ask follow-up questions.

    Current logic:
    - If description is too short (< 300 chars) OR
    - If it contains common vague phrases ("not working", "help", "can't login", etc.)

    This is intentionally simple and can be tuned based on real user behavior.
    """
    d = (description or "").strip().lower()
    too_short = len(d) < 300
    too_short = len(d) < 300
    generic_phrases = any(p in d for p in [
        "cant login", "can't login", "cannot login", "login problem",
        "problem with the internet", "internet problem",
        "doesn't work", "not working", "help",
    ])
    return too_short or generic_phrases

def generate_followup_questions(title: str, description: str) -> dict:
    """
    Ask the OpenAI model to generate a small set of follow-up questions.

    Output is forced into a strict JSON schema so the frontend can render it reliably.

    Returns:
      dict like:
        {
          "questions": [
            {"id": "...", "type": "...", "question": "...", "choices": [...], "required": true},
            ...
          ]
        }
    """
    schema = {
        "name": "followup_questions",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "questions": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 7,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            # id lets the frontend map answers back to the right question.
                            "id": {"type": "string"},
                            # question "type" controls how the frontend renders the UI.
                            "type": {"type": "string", "enum": ["yes_no", "multiple_choice", "free_text"]},
                            "question": {"type": "string"},
                            # Always present. For yes_no/free_text it must be [] (empty array).
                            "choices": {"type": "array", "items": {"type": "string"}},
                            "required": {"type": "boolean"}
                        },
                        "required": ["id", "type", "question", "choices", "required"]
                    }
                }
            },
            "required": ["questions"]
        }
    }

    try:
        # Using the Responses API with JSON Schema output enforcement.
        # Temperature is low to reduce randomness.
        resp = client.responses.create(
            model="gpt-4.1-mini",
            input=[
                {
                    "role": "system",
                    "content": (
                        "You are an IT helpdesk triage assistant. "
                        "Ask the minimum number of targeted follow-up questions to diagnose the issue. "
                        "Prefer multiple-choice when possible. Never ask for passwords or sensitive secrets. "
                        "Always include a 'choices' array in each question. "
                        "If the question is not multiple-choice, set choices to an empty array."
                    )
                },
                {
                    "role": "user",
                    "content": f"Title: {title}\nDescription: {description}\nReturn follow-up questions."
                }
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": schema["name"],
                    "schema": schema["schema"],
                    "strict": True
                }
            },
            temperature=0.2,
        )

        # `resp.output_text` is expected to be JSON because of json_schema formatting.
        return json.loads(resp.output_text)

    except Exception as e:
        # Raise a clear error up to the route handler.
        raise RuntimeError(f"OpenAI followups failed: {e}")

def improve_ticket_description(title: str, original_description: str, answers: dict) -> dict:
    """
    Produce the final "improved" ticket from the original description + follow-up answers.

    Returns a structured dict including:
      - improved_description: rewritten final description
      - category_guess: a best-effort category label
      - urgency_guess: low/medium/high
      - missing_info: list of remaining info gaps
    """
    schema = {
        "name": "final_ticket",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "improved_description": {"type": "string"},
                "category_guess": {"type": "string"},
                "urgency_guess": {"type": "string", "enum": ["low", "medium", "high"]},
                "missing_info": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["improved_description", "category_guess", "urgency_guess", "missing_info"]
        }
    }

    try:
        resp = client.responses.create(
            model="gpt-4.1-mini",
            input=[
                {
                    "role": "system",
                    "content": (
                        "Rewrite IT support tickets into clear, actionable descriptions. "
                        "Never include or request passwords or secrets."
                    )
                },
                {
                    "role": "user",
                    "content": (
                        f"Title: {title}\n"
                        f"Original description:\n{original_description}\n\n"
                        f"Follow-up answers JSON:\n{json.dumps(answers, ensure_ascii=False)}\n\n"
                        "Produce the final structured result."
                    )
                }
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": schema["name"],
                    "schema": schema["schema"],
                    "strict": True
                }
            },
            temperature=0.2,
        )
        return json.loads(resp.output_text)

    except Exception as e:
        raise RuntimeError(f"OpenAI finalize failed: {e}")


# Routes

@app.get("/api/ping")
@app.get("/api/ping")
def ping():
    """Health check endpoint: returns {"ok": true} if the server is running."""
    return jsonify({"ok": True})

@app.post("/api/draft/start")
def start_draft():
    """
    Start (or reset) a draft session for a user.

    Expected JSON body:
      { "user_id": 1 }

    Behavior:
    - Creates a new row in ticket_drafts if it doesn't exist.
    - If it already exists, resets it back to a fresh 'draft' state.
    - Clears any previous stored title/description/questions/answers for that user.
    """
    data = request.get_json(force=True)
    user_id = data.get("user_id")
    table_choice = data.get("table")

    # validate table choice (1-5); default to 1
    try:
        table_choice = int(table_choice) if table_choice is not None else 1
    except Exception:
        raise ValueError("table must be an integer between 1 and 5")    
    if table_choice < 1 or table_choice > 5:
        return jsonify({"error": "table must be integer between 1 and 5"}), 400

    # Restricting user_id to 1..99 keeps this demo app simple for testing
    if not isinstance(user_id, int) or not (1 <= user_id <= 99):
        return jsonify({"error": "user_id must be an integer between 1 and 99"}), 400

    conn = get_conn()
    t = now_s()

    # "Upsert" behavior:
    # - INSERT a new draft row for this user
    # - OR if user_id already exists, overwrite/reset that row to a new draft state
    conn.execute(
        """
        INSERT INTO ticket_drafts (
            user_id, state,
            draft_title, draft_description, ai_questions_json, ai_answers_json, ai_turns, started_at, log_table
        )
        VALUES (?, 'draft', NULL, NULL, NULL, NULL, 0, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            state = 'draft',
            draft_title = NULL,
            draft_description = NULL,
            ai_questions_json = NULL,
            ai_answers_json = NULL,
            ai_turns = 0,
            started_at = excluded.started_at,
            log_table = excluded.log_table
        """,
        (user_id, t, table_choice)
    )

    conn.commit()
    conn.close()
    return jsonify({"user_id": user_id})

@app.post("/api/tickets")
@app.post("/api/tickets")
def create_ticket():
    """
    Submit a ticket WITHOUT using AI follow-ups.

    Expected JSON body:
      { "user_id": 1, "title": "Printer issue", "description": "..." }

    Important:
    - This endpoint requires that a draft exists in state='draft'.
      The intended UI flow is: user clicks Confirm -> /api/draft/start, then submits.
    - This endpoint always stores ai_used = 0.
    """
    data = request.get_json(force=True)

    user_id = data.get("user_id")
    title = (data.get("title") or "").strip()
    description = (data.get("description") or "").strip()

    if not isinstance(user_id, int) or not (1 <= user_id <= 99):
        return jsonify({"error": "user_id must be an integer between 1 and 99"}), 400
    if not title or not description:
        return jsonify({"error": "title and description required"}), 400

    conn = get_conn()

    # Ensure there is an active draft (prevents submitting without starting the flow).
    draft = conn.execute(
        "SELECT * FROM ticket_drafts WHERE user_id = ? AND state = 'draft'",
        (user_id,)
    ).fetchone()

    if not draft:
        conn.close()
        return jsonify({"error": "No active draft for this user. Click Confirm first."}), 400

    created_at = now_s()
    time_spent = created_at - draft["started_at"]

    # Determine which tickets table to use (tickets_1 .. tickets_5)
    tbl_idx = draft["log_table"]
    try:
        tbl_idx = int(tbl_idx)
    except Exception:
        tbl_idx = 1
    if tbl_idx < 1 or tbl_idx > 5:
        tbl_idx = 1
    tickets_table = f"tickets_{tbl_idx}"

    conn.execute(
        f"INSERT INTO {tickets_table} (user_id, title, description, time_to_submit_ms, ai_used, status) VALUES (?, ?, ?, ?, 0, 'open')",
        (user_id, title, description, time_spent)
    )

    # Mark the draft as submitted so it can't be reused accidentally.
    conn.execute(
        "UPDATE ticket_drafts SET state='submitted', submitted_at=? WHERE user_id=?",
        (created_at, user_id)
    )

    conn.commit()
    conn.close()
    return jsonify({"user_id": user_id, "time_to_submit_ms": time_spent}), 201

@app.get("/api/tickets")
@app.get("/api/tickets")
def list_tickets():
    """
    List the most recent tickets (up to 100).

    Returns an array like:
      [
        { "user_id": 1, "title": "...", "created_at": ..., "time_to_submit_ms": ..., "status": "open" },
        ...
      ]
    """
    conn = get_conn()
    # Aggregate tickets from all five tables
    rows = conn.execute(
        """
        SELECT user_id, title, time_to_submit_ms, status FROM tickets_1
        UNION ALL
        SELECT user_id, title, time_to_submit_ms, status FROM tickets_2
        UNION ALL
        SELECT user_id, title, time_to_submit_ms, status FROM tickets_3
        UNION ALL
        SELECT user_id, title, time_to_submit_ms, status FROM tickets_4
        UNION ALL
        SELECT user_id, title, time_to_submit_ms, status FROM tickets_5
        ORDER BY user_id DESC
        LIMIT 100
        """
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.post("/api/ai/followups")
def ai_followups():
    """
    Decide if we need follow-up questions, and if so generate them via OpenAI.

    Expected JSON body:
      { "user_id": 1, "title": "Can't login", "description": "..." }

    Flow:
    1) Validate user + require an active draft.
    2) Save the user's title/description into the draft table (so finalize can use it).
    3) If description is already detailed enough -> return needs_followup=false
    4) Otherwise call OpenAI to generate questions, store them, return them to frontend.

    Response examples:
      { "needs_followup": false }
    or
      { "needs_followup": true, "questions": [ ... ] }
    """
    data = request.get_json(force=True)
    user_id = data.get("user_id")
    title = (data.get("title") or "").strip()
    description = (data.get("description") or "").strip()

    if not isinstance(user_id, int) or not (1 <= user_id <= 99):
        return jsonify({"error": "user_id must be an integer between 1 and 99"}), 400
    if not title or not description:
        return jsonify({"error": "title and description required"}), 400

    conn = get_conn()

    # Must have an active draft for the current user.
    draft = conn.execute(
        "SELECT * FROM ticket_drafts WHERE user_id=? AND state='draft'",
        (user_id,)
    ).fetchone()

    if not draft:
        conn.close()
        return jsonify({"error": "No active draft for this user. Click Confirm first."}), 400

    # Persist what the user typed into the draft so /ai/finalize can use it later.
    conn.execute(
        "UPDATE ticket_drafts SET draft_title=?, draft_description=? WHERE user_id=?",
        (title, description, user_id)
    )
    conn.commit()

    # If the description looks sufficiently detailed, skip AI questions.
    if not should_ask_followups(description):
        conn.close()
        return jsonify({"needs_followup": False})

    # Generate follow-up questions from OpenAI.
    try:
        q = generate_followup_questions(title, description)
    except Exception as e:
        conn.close()
        # 502 indicates a bad gateway / upstream error (OpenAI in this case).
        return jsonify({"error": str(e)}), 502

    # Store questions in the draft and track that we used AI (ai_turns++ for analytics/debugging).
    conn.execute(
        """
        UPDATE ticket_drafts
        SET ai_questions_json=?,
            ai_turns=ai_turns+1
        WHERE user_id=?
        """,
        (json.dumps(q, ensure_ascii=False), user_id)
    )

    conn.commit()
    conn.close()

    # Return only the "questions" list to keep frontend simpler.
    return jsonify({"needs_followup": True, "questions": q["questions"]})

@app.post("/api/ai/finalize")
def ai_finalize():
    """
    Finalize a ticket using the AI follow-up answers.

    Expected JSON body:
      { "user_id": 1, "answers": { "<question_id>": "<answer>", ... } }

    Flow:
    1) Validate user + answers.
    2) Load the current draft (must exist and have saved title/description).
    3) Call OpenAI to produce an improved description + metadata.
    4) Insert ticket with ai_used=1.
    5) Mark draft as submitted and store answers JSON for audit/debug.

    Response:
      {
        "user_id": 1,
        "time_to_submit_ms": ...,
        "final": {
          "improved_description": "...",
          "category_guess": "...",
          "urgency_guess": "low|medium|high",
          "missing_info": [...]
        }
      }
    """
    data = request.get_json(force=True)
    user_id = data.get("user_id")
    answers = data.get("answers") or {}

    if not isinstance(user_id, int) or not (1 <= user_id <= 99):
        return jsonify({"error": "user_id must be an integer between 1 and 99"}), 400
    if not isinstance(answers, dict) or not answers:
        return jsonify({"error": "answers must be a non-empty object"}), 400

    conn = get_conn()

    # Load the draft and ensure it contains the user's original title/description.
    draft = conn.execute(
        "SELECT * FROM ticket_drafts WHERE user_id=? AND state='draft'",
        (user_id,)
    ).fetchone()

    if not draft or not draft["draft_title"] or not draft["draft_description"]:
        conn.close()
        return jsonify({"error": "No draft content found. Submit the form first."}), 400

    title = draft["draft_title"]
    original_description = draft["draft_description"]

    # Ask OpenAI to rewrite the ticket + add metadata.
    try:
        final = improve_ticket_description(title, original_description, answers)
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 502

    improved_description = final["improved_description"]

    created_at = now_s()
    time_spent = created_at - draft["started_at"]

    # Insert into the same chosen tickets_N table as the draft
    tbl_idx = draft["log_table"] or 1
    try:
        tbl_idx = int(tbl_idx)
    except Exception:
        tbl_idx = 1
    if tbl_idx < 1 or tbl_idx > 5:
        tbl_idx = 1
    tickets_table = f"tickets_{tbl_idx}"

    conn.execute(
        f"INSERT INTO {tickets_table} (user_id, title, description, time_to_submit_ms, ai_used, status) VALUES (?, ?, ?, ?, 1, 'open')",
        (user_id, title, improved_description, time_spent)
    )

    # Mark the draft as submitted and store the answers for traceability.
    conn.execute(
        """
        UPDATE ticket_drafts
        SET state='submitted',
            ai_answers_json=?,
            ai_turns=ai_turns+1,
            submitted_at=?
        WHERE user_id=?
        """,
        (json.dumps(answers, ensure_ascii=False), created_at, user_id)
    )

    conn.commit()
    conn.close()

    return jsonify({
        "user_id": user_id,
        "time_to_submit_ms": time_spent,
        "final": final
    }), 201

# -----------------------------
# Local dev server entry point
# -----------------------------
if __name__ == "__main__":
    # Only runs when executing this file directly:
    #   python backend.py
    print("Starting backend on http://127.0.0.1:5000")

    # Ensure DB schema exists before serving requests.
    init_db()

    # Flask dev server (debug=True enables auto-reload + verbose errors).
    app.run(host="127.0.0.1", port=5000, debug=True)
