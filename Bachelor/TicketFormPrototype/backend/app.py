from flask import Flask, request, jsonify
from flask_cors import CORS

import time
import os
import sqlite3
import json
import requests

from dotenv import load_dotenv
from pathlib import Path

# -----------------------------
# Environment / configuration
# -----------------------------
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path, override=True)

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "..", "tickets.db")

# AI provider:
#   stub    = local fallback for testing
#   copilot = Direct Line integration
AI_PROVIDER = os.getenv("AI_PROVIDER", "stub").strip().lower()


def now_s() -> int:
    return int(time.time())


def get_conn():
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


def init_db():
    conn = get_conn()

    conn.executescript("""
    PRAGMA foreign_keys = ON;

    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        username TEXT NOT NULL UNIQUE
    );

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
        log_table INTEGER,
        FOREIGN KEY (user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS tickets_1 (
        user_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        time_to_submit_ms INTEGER,
        ai_used INTEGER DEFAULT 0,
        status TEXT DEFAULT 'open',
        FOREIGN KEY (user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS tickets_2 (
        user_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        time_to_submit_ms INTEGER,
        ai_used INTEGER DEFAULT 0,
        status TEXT DEFAULT 'open',
        FOREIGN KEY (user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS tickets_3 (
        user_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        time_to_submit_ms INTEGER,
        ai_used INTEGER DEFAULT 0,
        status TEXT DEFAULT 'open',
        FOREIGN KEY (user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS tickets_4 (
        user_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        time_to_submit_ms INTEGER,
        ai_used INTEGER DEFAULT 0,
        status TEXT DEFAULT 'open',
        FOREIGN KEY (user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS tickets_5 (
        user_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        time_to_submit_ms INTEGER,
        ai_used INTEGER DEFAULT 0,
        status TEXT DEFAULT 'open',
        FOREIGN KEY (user_id) REFERENCES users(id)
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
    if "started_at" not in cols:
        add_col("ALTER TABLE ticket_drafts ADD COLUMN started_at INTEGER;")
    if "last_activity_at" not in cols:
        add_col("ALTER TABLE ticket_drafts ADD COLUMN last_activity_at INTEGER;")
    if "submitted_at" not in cols:
        add_col("ALTER TABLE ticket_drafts ADD COLUMN submitted_at INTEGER;")
    if "log_table" not in cols:
        add_col("ALTER TABLE ticket_drafts ADD COLUMN log_table INTEGER;")

    conn.execute(
        """
        UPDATE ticket_drafts
        SET last_activity_at = COALESCE(last_activity_at, started_at, ?)
        WHERE last_activity_at IS NULL
        """,
        (now_s(),)
    )
    conn.commit()
    conn.close()


# =============================================================================
# AI CONVERSATION ENGINE
# =============================================================================

SYSTEM_PROMPT = """You are an IT helpdesk triage assistant. Your ONLY job is to check
whether a ticket is missing information, and if so, ask for exactly what is missing.

IMPORTANT: The user has already written a description. READ IT CAREFULLY before doing anything.
If the description already answers the required criteria, you MUST set done=true immediately.
Do NOT ask about things that are already stated in the description.

There are two ticket types:

1. IT PROBLEM — complete when ALL THREE are answered:
   A) WHERE/WHAT is the problem? (specific area: network, hardware, software, email, etc.
      AND what specifically — e.g. "cannot connect to WiFi" not just "network")
   B) WHEN did it start or how often does it happen?
   C) WHAT has the user already tried? ("nothing yet" is a valid answer)

2. ITEM/PRODUCT REQUEST — complete when you know specifically what item is being requested.

Respond ONLY with valid JSON in exactly one of these two formats:

If something is genuinely missing:
{
  "done": false,
  "question": "Your question here",
  "type": "multiple_choice",
  "choices": ["Option 1", "Option 2", "Option 3"]
}

If the ticket is complete:
{
  "done": true,
  "question": null,
  "type": null,
  "choices": []
}

Rules:
- Read the full description first. If it already covers the criteria, return done=true.
- NEVER ask about something already mentioned in the description or a previous answer.
- Only ask ONE question per turn — the single most important missing piece.
- Prefer multiple_choice whenever the answer space is predictable.
- Use free_text only when the answer is truly open-ended.
- Never ask for passwords or sensitive information.
- Maximum 3 questions total. After 3 questions you MUST return done=true.
"""


def _safe_json_from_text(raw: str) -> dict:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
        raw = raw.rsplit("```", 1)[0].strip()
    return json.loads(raw)


def _local_stub_response(prompt: str) -> str:
    lower = prompt.lower()

    if "improved_description" in prompt and "urgency_guess" in prompt:
        return json.dumps({
            "improved_description": "Brukeren har sendt inn en forespørsel til IT servicedesk. Beskrivelsen er samlet og strukturert for videre behandling.",
            "category_guess": "IT request",
            "urgency_guess": "medium",
            "missing_info": []
        })

    if "what has the user already tried" in lower or "hva har du prøvd" in lower:
        return json.dumps({
            "done": False,
            "question": "Hva har du prøvd så langt for å løse problemet?",
            "type": "free_text",
            "choices": []
        })

    return json.dumps({
        "done": True,
        "question": None,
        "type": None,
        "choices": []
    })


def call_ai(prompt: str) -> str:
    if AI_PROVIDER == "stub":
        return _local_stub_response(prompt)

    if AI_PROVIDER == "copilot":
        
        if not secret:
            raise RuntimeError("Missing COPILOT_DIRECTLINE_SECRET in .env")

        try:
            # 1. Start Direct Line conversation
            conv_res = requests.post(
                "https://directline.botframework.com/v3/directline/conversations",
                headers={"Authorization": f"Bearer {secret}"},
                timeout=20
            )
            conv_res.raise_for_status()
            conv_id = conv_res.json()["conversationId"]

            # 2. Send prompt to bot
            send_res = requests.post(
                f"https://directline.botframework.com/v3/directline/conversations/{conv_id}/activities",
                headers={
                    "Authorization": f"Bearer {secret}",
                    "Content-Type": "application/json"
                },
                json={
                    "type": "message",
                    "from": {"id": "user"},
                    "text": prompt
                },
                timeout=20
            )
            send_res.raise_for_status()

            # 3. Wait briefly and fetch bot reply
            time.sleep(1.5)

            reply = requests.get(
                f"https://directline.botframework.com/v3/directline/conversations/{conv_id}/activities",
                headers={"Authorization": f"Bearer {secret}"},
                timeout=20
            )
            reply.raise_for_status()
            activities = reply.json().get("activities", [])

            for act in reversed(activities):
                if act.get("from", {}).get("id") != "user":
                    return act.get("text", "{}")

            return "{}"

        except Exception as e:
            raise RuntimeError(f"Copilot DirectLine error: {e}")

    raise RuntimeError(f"Unknown AI_PROVIDER: {AI_PROVIDER}")


def improve_ticket_description(title: str, original_description: str, answers: dict) -> dict:
    prompt = (
        "Rewrite IT support tickets into clear, actionable descriptions. "
        "Never include or request passwords or secrets.\n\n"
        "Return ONLY valid JSON with this shape:\n"
        "{\n"
        '  "improved_description": "string",\n'
        '  "category_guess": "string",\n'
        '  "urgency_guess": "low|medium|high",\n'
        '  "missing_info": ["string"]\n'
        "}\n\n"
        f"Title: {title}\n"
        f"Original description:\n{original_description}\n\n"
        f"Follow-up Q&A:\n{json.dumps(answers, ensure_ascii=False)}\n"
    )

    raw = call_ai(prompt)
    result = _safe_json_from_text(raw)

    return {
        "improved_description": result.get("improved_description", original_description),
        "category_guess": result.get("category_guess", title),
        "urgency_guess": result.get("urgency_guess", "medium"),
        "missing_info": result.get("missing_info", []),
    }


def ai_chat_turn(title: str, description: str, conversation: list, questions_asked: int) -> dict:
    context_parts = [
        SYSTEM_PROMPT,
        "",
        f"Ticket category: {title}",
        f"User description: {description}",
    ]

    if conversation:
        context_parts.append("\nConversation so far:")
        for i, turn in enumerate(conversation, 1):
            context_parts.append(f"Q{i}: {turn.get('question', '')}")
            context_parts.append(f"A{i}: {turn.get('answer', '')}")

    if questions_asked >= 3:
        context_parts.append("\nYou have already asked 3 questions. You MUST return done=true now.")
    else:
        remaining = 3 - questions_asked
        context_parts.append(
            f"\nSTEP 1 — Re-read the user description above."
            f"\nSTEP 2 — Check: does the description already answer the required criteria?"
            f"\nSTEP 3 — If yes, return done=true. If no, ask about the single most important gap."
            f"\n({questions_asked}/3 questions asked so far. You have {remaining} remaining.)"
        )

    raw = call_ai("\n".join(context_parts))
    result = _safe_json_from_text(raw)

    done = bool(result.get("done", False))
    question = result.get("question") or None
    qtype = result.get("type") or "free_text"
    choices = result.get("choices") or []

    if done or not question:
        return {"done": True, "question": None, "type": None, "choices": []}

    return {"done": False, "question": question, "type": qtype, "choices": choices}


# =============================================================================
# ROUTES
# =============================================================================

@app.get("/api/ping")
def ping():
    return jsonify({"ok": True})


@app.post("/api/draft/start")
def start_draft():
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
    conn.execute(
        "INSERT OR IGNORE INTO users (id, username) VALUES (?, ?)",
        (user_id, f"user{user_id}")
    )

    t = now_s()

    conn.execute(
        """
        INSERT INTO ticket_drafts (
            user_id, state,
            draft_title, draft_description, ai_questions_json, ai_answers_json, ai_turns, started_at, last_activity_at, log_table
        )
        VALUES (?, 'draft', NULL, NULL, NULL, NULL, 0, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            state = 'draft',
            draft_title = NULL,
            draft_description = NULL,
            ai_questions_json = NULL,
            ai_answers_json = NULL,
            ai_turns = 0,
            started_at = excluded.started_at,
            last_activity_at = excluded.last_activity_at,
            log_table = excluded.log_table
        """,
        (user_id, t, t, table_choice)
    )

    conn.commit()
    conn.close()
    return jsonify({"user_id": user_id})


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
        current_title = draft["draft_title"] or ""
        current_description = draft["draft_description"] or ""

        if not current_title or not current_description:
            conn.close()
            return jsonify({"error": "No draft content found. Submit the form first."}), 400

        conversation = json.loads(draft["ai_questions_json"] or "[]")

        if conversation and prior_answer and "answer" not in conversation[-1]:
            conversation[-1]["answer"] = prior_answer
            conn.execute(
                "UPDATE ticket_drafts SET ai_questions_json=? WHERE user_id=?",
                (json.dumps(conversation), user_id)
            )
            conn.commit()

    questions_asked = len([t for t in conversation if "answer" in t])

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

    answers = {
        f"q{i+1}": f"Q: {t.get('question', '')} A: {t.get('answer', '(no answer)')}"
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


if __name__ == "__main__":
    print("Starting backend on http://127.0.0.1:5000")
    init_db()
    app.run(host="127.0.0.1", port=5000, debug=True)