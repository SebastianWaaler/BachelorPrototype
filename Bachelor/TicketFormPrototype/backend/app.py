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
        ai_turns INTEGER DEFAULT 0,
        state TEXT NOT NULL CHECK (state IN ('draft','submitted','abandoned')),
        draft_title TEXT,
        draft_description TEXT,
        ai_questions_json TEXT,
        ai_answers_json TEXT,
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


# =============================================================================
# AI CONVERSATION ENGINE
# =============================================================================
#
# Design: one endpoint (/api/ai/chat) drives the entire question loop.
# The full conversation (questions + answers) is stored as JSON in the draft
# so the AI sees the complete context on every call and can make a genuinely
# informed decision about what to ask next or whether it has enough info.
#
# Flow:
#   1. Frontend submits title + description  -> POST /api/ai/chat (no prior_answer)
#   2. AI returns a question OR done=true
#   3. Frontend shows the question, user answers -> POST /api/ai/chat (with prior_answer)
#   4. Repeat until done=true (max 3 questions total)
#   5. Frontend calls POST /api/ai/finalize to write the final ticket
# =============================================================================

SYSTEM_PROMPT = """You are an IT helpdesk triage assistant. Your job is to decide
whether a support ticket has enough information, and if not, ask for what is missing.
The ticket can be IT-related problems, or it can be a request for an item or product.
Make sure you prefer multiple choice options.

If it is an IT-related problem, the ticket is complete when ALL THREE of the following are clearly answered:
  A) WHERE exactly is the problem? (network, hardware, software, email -
     NOT just "a problem" or "it doesn't work")
  B) WHEN does it happen? (since when, how often, what were you doing -
     "since when is" the most important)
  C) WHAT has the user already tried? (restarted, reinstalled, checked cables, etc.
     - "I haven't tried anything" is a perfectly acceptable answer for C)

If it is a request for an item/product, the ticket is complete when you know what the item is.     

You will be given the ticket description and the conversation history so far.

First, carefully check whether the description ALREADY answers A, B, and C.
If the description is detailed and specific, set done=true immediately — do NOT ask
questions just for the sake of asking them.

Respond ONLY with valid JSON in exactly this format:
{
  "done": false,
  "question": "Your question text here",
  "type": "multiple_choice",
  "choices": ["Option 1", "Option 2", "Option 3"]
}

OR when the ticket is complete:
{
  "done": true,
  "question": null,
  "type": null,
  "choices": []
}

Rules:
- Evaluate the FULL description first. If A, B, and C are all clearly answered, set done=true.
- Only set done=false if at least one criterion is genuinely missing or too vague to act on.
- A single word or vague phrase does NOT satisfy a criterion. "Network" does not satisfy A —
  you need to know specifically what the network problem is.
- Only ask ONE question per turn, targeting the single most important missing criterion.
- Use multiple_choice when the answer space is predictable (strongly preferred).
- Use free_text only when the answer cannot be predicted.
- Never ask for passwords or sensitive information.
- Maximum 3 questions total. After 3 questions, always set done=true.
"""


def improve_ticket_description(title: str, original_description: str, answers: dict) -> dict:
    """
    Produce the final improved ticket from the original description + conversation answers.
    Returns: { improved_description, category_guess, urgency_guess, missing_info }
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
                        f"Follow-up Q&A:\n{json.dumps(answers, ensure_ascii=False)}\n\n"
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


def ai_chat_turn(title: str, description: str, conversation: list, questions_asked: int) -> dict:
    """
    Run one turn of the triage conversation.

    Args:
        title: ticket title/category
        description: original user description
        conversation: list of {"question": ..., "answer": ...} dicts from previous turns
        questions_asked: how many questions have already been asked

    Returns:
        dict with keys: done (bool), question (str|None), type (str|None), choices (list)
    """
    # Build the user message showing full context
    context_parts = [
        f"Ticket category: {title}",
        f"User description: {description}",
    ]

    if conversation:
        context_parts.append("\nConversation so far:")
        for i, turn in enumerate(conversation, 1):
            context_parts.append(f"  Q{i}: {turn['question']}")
            context_parts.append(f"  A{i}: {turn['answer']}")

    if questions_asked >= 3:
        context_parts.append("\nYou have already asked 3 questions. You MUST set done=true now.")
    else:
        remaining = 3 - questions_asked
        context_parts.append(f"\nYou may ask at most {remaining} more question(s).")
        context_parts.append("Check criteria A, B, C. If all are satisfied, set done=true. Otherwise ask the most important missing question.")

    user_message = "\n".join(context_parts)

    try:
        resp = client.responses.create(
            model="gpt-4.1-mini",
            input=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message}
            ],
            text={"format": {"type": "text"}},
            temperature=0.2,
        )

        raw = resp.output_text.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1]
            raw = raw.rsplit("```", 1)[0].strip()

        result = json.loads(raw)

        # Validate and normalise the response
        done = bool(result.get("done", False))
        question = result.get("question") or None
        qtype = result.get("type") or "free_text"
        choices = result.get("choices") or []

        if done:
            return {"done": True, "question": None, "type": None, "choices": []}

        if not question:
            # AI returned done=false but no question — treat as done to avoid infinite loop
            return {"done": True, "question": None, "type": None, "choices": []}

        return {"done": False, "question": question, "type": qtype, "choices": choices}

    except Exception as e:
        raise RuntimeError(f"AI chat turn failed: {e}")


# =============================================================================
# ROUTES
# =============================================================================

@app.get("/api/ping")
def ping():
    """Health check endpoint: returns {"ok": true} if the server is running."""
    return jsonify({"ok": True})


@app.post("/api/draft/start")
def start_draft():
    """
    Start (or reset) a draft session for a user.
    Body: { "user_id": 1, "table": 1 }
    """
    data = request.get_json(force=True)
    user_id = data.get("user_id")
    table_choice = data.get("table")

    try:
        table_choice = int(table_choice) if table_choice is not None else 1
    except Exception:
        return jsonify({"error": "table must be an integer between 1 and 5"}), 400
    if table_choice < 1 or table_choice > 5:
        return jsonify({"error": "table must be integer between 1 and 5"}), 400

    if not isinstance(user_id, int) or not (1 <= user_id <= 99):
        return jsonify({"error": "user_id must be an integer between 1 and 99"}), 400

    conn = get_conn()
    t = now_s()

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
def create_ticket():
    """
    Submit a ticket WITHOUT AI (description was already detailed enough).
    Body: { "user_id": 1, "title": "...", "description": "..." }
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

    draft = conn.execute(
        "SELECT * FROM ticket_drafts WHERE user_id = ? AND state = 'draft'",
        (user_id,)
    ).fetchone()

    if not draft:
        conn.close()
        return jsonify({"error": "No active draft for this user. Click Confirm first."}), 400

    created_at = now_s()
    time_spent = created_at - draft["started_at"]

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

    conn.execute(
        "UPDATE ticket_drafts SET state='submitted', submitted_at=? WHERE user_id=?",
        (created_at, user_id)
    )

    conn.commit()
    conn.close()
    return jsonify({"user_id": user_id, "time_to_submit_ms": time_spent, "log_table": tbl_idx}), 201


@app.get("/api/tickets")
def list_tickets():
    """List the most recent tickets (up to 100) from all tables."""
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT user_id, title, time_to_submit_ms, status FROM tickets_1
        UNION ALL SELECT user_id, title, time_to_submit_ms, status FROM tickets_2
        UNION ALL SELECT user_id, title, time_to_submit_ms, status FROM tickets_3
        UNION ALL SELECT user_id, title, time_to_submit_ms, status FROM tickets_4
        UNION ALL SELECT user_id, title, time_to_submit_ms, status FROM tickets_5
        ORDER BY user_id DESC LIMIT 100
        """
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.post("/api/ai/chat")
def ai_chat():
    """
    Single endpoint that drives the entire triage conversation.

    First call (no prior_answer):
      Body: { "user_id": 1, "title": "...", "description": "..." }

    Subsequent calls (after user answers a question):
      Body: { "user_id": 1, "prior_answer": "user's answer to the last question" }

    Response:
      { "done": false, "question": "...", "type": "multiple_choice", "choices": [...] }
    or
      { "done": true }  -> frontend should call /api/ai/finalize next
    """
    data = request.get_json(force=True)
    user_id = data.get("user_id")
    prior_answer = (data.get("prior_answer") or "").strip()
    title = (data.get("title") or "").strip()
    description = (data.get("description") or "").strip()

    if not isinstance(user_id, int) or not (1 <= user_id <= 99):
        return jsonify({"error": "user_id must be an integer between 1 and 99"}), 400

    conn = get_conn()

    draft = conn.execute(
        "SELECT * FROM ticket_drafts WHERE user_id=? AND state='draft'",
        (user_id,)
    ).fetchone()

    if not draft:
        conn.close()
        return jsonify({"error": "No active draft. Click Confirm first."}), 400

    # --- First call: save title + description, start fresh conversation ---
    if title and description:
        conn.execute(
            "UPDATE ticket_drafts SET draft_title=?, draft_description=?, ai_questions_json=? WHERE user_id=?",
            (title, description, json.dumps([]), user_id)
        )
        conn.commit()
        conversation = []
        current_title = title
        current_description = description
    else:
        # --- Subsequent call: load saved state and append prior answer ---
        current_title = draft["draft_title"] or ""
        current_description = draft["draft_description"] or ""

        if not current_title or not current_description:
            conn.close()
            return jsonify({"error": "No draft content found. Submit the form first."}), 400

        conversation = json.loads(draft["ai_questions_json"] or "[]")

        # The last entry in conversation has no answer yet — fill it in
        if conversation and prior_answer and "answer" not in conversation[-1]:
            conversation[-1]["answer"] = prior_answer
            conn.execute(
                "UPDATE ticket_drafts SET ai_questions_json=? WHERE user_id=?",
                (json.dumps(conversation), user_id)
            )
            conn.commit()

    questions_asked = len([t for t in conversation if "answer" in t])

    # Ask the AI what to do next
    try:
        result = ai_chat_turn(current_title, current_description, conversation, questions_asked)
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 502

    if result["done"]:
        conn.execute(
            "UPDATE ticket_drafts SET ai_turns=ai_turns+1 WHERE user_id=?",
            (user_id,)
        )
        conn.commit()
        conn.close()
        return jsonify({"done": True})

    # Save the new question (without answer yet) to the conversation
    conversation.append({"question": result["question"]})
    conn.execute(
        "UPDATE ticket_drafts SET ai_questions_json=?, ai_turns=ai_turns+1 WHERE user_id=?",
        (json.dumps(conversation), user_id)
    )
    conn.commit()
    conn.close()

    return jsonify({
        "done": False,
        "question": result["question"],
        "type": result["type"],
        "choices": result["choices"]
    })


@app.post("/api/ai/finalize")
def ai_finalize():
    """
    Build the final improved ticket from the full conversation and submit it.
    Body: { "user_id": 1 }
    """
    data = request.get_json(force=True)
    user_id = data.get("user_id")

    if not isinstance(user_id, int) or not (1 <= user_id <= 99):
        return jsonify({"error": "user_id must be an integer between 1 and 99"}), 400

    conn = get_conn()

    draft = conn.execute(
        "SELECT * FROM ticket_drafts WHERE user_id=? AND state='draft'",
        (user_id,)
    ).fetchone()

    if not draft or not draft["draft_title"] or not draft["draft_description"]:
        conn.close()
        return jsonify({"error": "No draft content found."}), 400

    title = draft["draft_title"]
    original_description = draft["draft_description"]
    conversation = json.loads(draft["ai_questions_json"] or "[]")

    # Build answers dict for improve_ticket_description (keyed by question text)
    answers = {
        f"q{i+1}": f"Q: {t.get('question','')} A: {t.get('answer','(no answer)')}"
        for i, t in enumerate(conversation)
        if "answer" in t
    }

    try:
        final = improve_ticket_description(title, original_description, answers)
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 502

    improved_description = final["improved_description"]
    created_at = now_s()
    time_spent = created_at - draft["started_at"]

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

    conn.execute(
        """
        UPDATE ticket_drafts
        SET state='submitted', ai_answers_json=?, ai_turns=ai_turns+1, submitted_at=?
        WHERE user_id=?
        """,
        (json.dumps(answers), created_at, user_id)
    )

    conn.commit()
    conn.close()

    return jsonify({
        "user_id": user_id,
        "time_to_submit_ms": time_spent,
        "log_table": tbl_idx,
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