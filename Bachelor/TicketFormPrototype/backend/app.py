# Python file for running the backend
from flask import Flask, request, jsonify
from flask_cors import CORS
import time
import os
import sqlite3
import json
from openai import OpenAI
from dotenv import load_dotenv
from pathlib import Path

# Force loading .env from backend folder (and override any existing env var)
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path, override=True)

# Debug: print only a safe prefix (do NOT print full key)
_loaded = os.getenv("OPENAI_API_KEY") or ""
print("Loaded key:", (_loaded[:15] + "...") if _loaded else "MISSING")
print("ENV PATH:", env_path)

app = Flask(__name__)
CORS(app)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "..", "tickets.db")

# OpenAI client uses OPENAI_API_KEY from env/.env
client = OpenAI()

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
      user_id INTEGER PRIMARY KEY,
      started_at INTEGER NOT NULL,
      last_activity_at INTEGER NOT NULL,
      ai_turns INTEGER DEFAULT 0,
      state TEXT NOT NULL CHECK (state IN ('draft','submitted','abandoned'))
    );

    CREATE TABLE IF NOT EXISTS tickets (
      user_id INTEGER NOT NULL,
      title TEXT NOT NULL,
      description TEXT NOT NULL,
      created_at INTEGER NOT NULL,
      time_to_submit_ms INTEGER,
      ai_used INTEGER DEFAULT 0,
      status TEXT DEFAULT 'open'
    );
    """)
    conn.commit()

    cols = {row["name"] for row in conn.execute("PRAGMA table_info(ticket_drafts)").fetchall()}

    def add_col(sql):
        try:
            conn.execute(sql)
            conn.commit()
        except sqlite3.OperationalError:
            pass

    if "draft_title" not in cols:
        add_col("ALTER TABLE ticket_drafts ADD COLUMN draft_title TEXT;")
    if "draft_description" not in cols:
        add_col("ALTER TABLE ticket_drafts ADD COLUMN draft_description TEXT;")
    if "ai_questions_json" not in cols:
        add_col("ALTER TABLE ticket_drafts ADD COLUMN ai_questions_json TEXT;")
    if "ai_answers_json" not in cols:
        add_col("ALTER TABLE ticket_drafts ADD COLUMN ai_answers_json TEXT;")

    conn.close()

def should_ask_followups(description: str) -> bool:
    d = (description or "").strip().lower()
    too_short = len(d) < 35
    generic_phrases = any(p in d for p in [
        "cant login", "can't login", "cannot login", "login problem",
        "problem with the internet", "internet problem",
        "doesn't work", "not working", "help",
    ])
    return too_short or generic_phrases

def generate_followup_questions(title: str, description: str) -> dict:
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
                            "id": {"type": "string"},
                            "type": {"type": "string", "enum": ["yes_no", "multiple_choice", "free_text"]},
                            "question": {"type": "string"},
                            "choices": {"type": "array", "items": {"type": "string"}},
                            "required": {"type": "boolean"}
                        },
                        # ✅ FIX: choices must be required when strict=True
                        # For yes_no / free_text, model should return choices: []
                        "required": ["id", "type", "question", "choices", "required"]
                    }
                }
            },
            "required": ["questions"]
        }
    }

    try:
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
        return json.loads(resp.output_text)
    except Exception as e:
        raise RuntimeError(f"OpenAI followups failed: {e}")

def improve_ticket_description(title: str, original_description: str, answers: dict) -> dict:
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

    conn.execute(
        """
        INSERT INTO ticket_drafts (
            user_id, started_at, last_activity_at, state,
            draft_title, draft_description, ai_questions_json, ai_answers_json, ai_turns
        )
        VALUES (?, ?, ?, 'draft', NULL, NULL, NULL, NULL, 0)
        ON CONFLICT(user_id) DO UPDATE SET
            started_at = excluded.started_at,
            last_activity_at = excluded.last_activity_at,
            state = 'draft',
            draft_title = NULL,
            draft_description = NULL,
            ai_questions_json = NULL,
            ai_answers_json = NULL,
            ai_turns = 0
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

@app.post("/api/ai/followups")
def ai_followups():
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
        "SELECT * FROM ticket_drafts WHERE user_id=? AND state='draft'",
        (user_id,)
    ).fetchone()

    if not draft:
        conn.close()
        return jsonify({"error": "No active draft for this user. Click Confirm first."}), 400

    conn.execute(
        "UPDATE ticket_drafts SET last_activity_at=?, draft_title=?, draft_description=? WHERE user_id=?",
        (now_ms(), title, description, user_id)
    )
    conn.commit()

    if not should_ask_followups(description):
        conn.close()
        return jsonify({"needs_followup": False})

    try:
        q = generate_followup_questions(title, description)
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 502

    conn.execute(
        """
        UPDATE ticket_drafts
        SET last_activity_at=?,
            ai_questions_json=?,
            ai_turns=ai_turns+1
        WHERE user_id=?
        """,
        (now_ms(), json.dumps(q, ensure_ascii=False), user_id)
    )

    conn.commit()
    conn.close()

    return jsonify({"needs_followup": True, "questions": q["questions"]})

@app.post("/api/ai/finalize")
def ai_finalize():
    data = request.get_json(force=True)
    user_id = data.get("user_id")
    answers = data.get("answers") or {}

    if not isinstance(user_id, int) or not (1 <= user_id <= 99):
        return jsonify({"error": "user_id must be an integer between 1 and 99"}), 400
    if not isinstance(answers, dict) or not answers:
        return jsonify({"error": "answers must be a non-empty object"}), 400

    conn = get_conn()
    draft = conn.execute(
        "SELECT * FROM ticket_drafts WHERE user_id=? AND state='draft'",
        (user_id,)
    ).fetchone()

    if not draft or not draft["draft_title"] or not draft["draft_description"]:
        conn.close()
        return jsonify({"error": "No draft content found. Submit the form first."}), 400

    title = draft["draft_title"]
    original_description = draft["draft_description"]

    try:
        final = improve_ticket_description(title, original_description, answers)
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 502

    improved_description = final["improved_description"]

    created_at = now_ms()
    time_spent = created_at - draft["started_at"]

    conn.execute(
        """
        INSERT INTO tickets (user_id, title, description, created_at, time_to_submit_ms, ai_used, status)
        VALUES (?, ?, ?, ?, ?, 1, 'open')
        """,
        (user_id, title, improved_description, created_at, time_spent)
    )

    conn.execute(
        """
        UPDATE ticket_drafts
        SET state='submitted',
            last_activity_at=?,
            ai_answers_json=?,
            ai_turns=ai_turns+1
        WHERE user_id=?
        """,
        (created_at, json.dumps(answers, ensure_ascii=False), user_id)
    )

    conn.commit()
    conn.close()

    return jsonify({
        "user_id": user_id,
        "time_to_submit_ms": time_spent,
        "final": final
    }), 201

if __name__ == "__main__":
    print("Starting backend on http://127.0.0.1:5000")
    init_db()
    app.run(host="127.0.0.1", port=5000, debug=True)
