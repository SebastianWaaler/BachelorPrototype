import os
import sys
import tempfile
from pathlib import Path
import json

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1] / "backend"))
import app as backend_app


@pytest.fixture
def client(monkeypatch):
    temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    temp_db_path = temp_db.name
    temp_db.close()  

    monkeypatch.setattr(backend_app, "DB_PATH", temp_db_path)

    backend_app.init_db()

    with backend_app.app.test_client() as client:
        yield client

    # Rydde opp i db etter test
    try:
        os.remove(temp_db_path)
    except PermissionError:
        pass


def test_ping_endpoint(client):
    res = client.get("/api/ping")

    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True


def test_create_draft(client):
    res = client.post("/api/draft/start", json={
        "user_id": 1,
        "table": 2
    })

    assert res.status_code == 200
    data = res.get_json()
    assert data["user_id"] == 1

    conn = backend_app.get_conn()
    row = conn.execute(
        "SELECT * FROM ticket_drafts WHERE user_id = ?",
        (1,)
    ).fetchone()
    conn.close()

    assert row is not None
    assert row["state"] == "draft"
    assert row["log_table"] == 2


def test_create_ticket_without_ai(client):
    client.post("/api/draft/start", json={
        "user_id": 2,
        "table": 1
    })

    res = client.post("/api/tickets", json={
        "user_id": 2,
        "title": "WiFi problem",
        "description": "Kan ikke koble til nettverk"
    })

    assert res.status_code == 201
    data = res.get_json()

    assert data["user_id"] == 2
    assert data["log_table"] == 1
    assert data["time_to_submit_ms"] >= 0

    conn = backend_app.get_conn()
    rows = conn.execute(
        "SELECT * FROM tickets_1 WHERE user_id = ?",
        (2,)
    ).fetchall()
    conn.close()

    assert len(rows) == 1
    assert rows[0]["title"] == "WiFi problem"


def test_list_tickets(client):
    client.post("/api/draft/start", json={
        "user_id": 3,
        "table": 3
    })

    client.post("/api/tickets", json={
        "user_id": 3,
        "title": "Printer virker ikke",
        "description": "Feil på printer"
    })

    res = client.get("/api/tickets")

    assert res.status_code == 200
    data = res.get_json()

    assert isinstance(data, list)
    assert any(ticket["title"] == "Printer virker ikke" for ticket in data)


def test_invalid_user_id_fails(client):
    res = client.post("/api/draft/start", json={
        "user_id": 150,
        "table": 1
    })

    assert res.status_code == 400
    data = res.get_json()
    assert "error" in data


def test_ticket_without_draft_fails(client):
    res = client.post("/api/tickets", json={
        "user_id": 5,
        "title": "Mangler skytilgang",
        "description": "Kommer ikke inn på OneDrive"
    })

    assert res.status_code == 400
    data = res.get_json()
    assert "No active draft" in data["error"]


#-----------------------------#
#AI testing 
#-----------------------------#

def test_ai_chat_returns_question_and_saves_it(client, monkeypatch):
    def fake_ai_chat_turn(title, description, conversation, questions_asked):
        return {
            "done": False,
            "question": "Når startet problemet?",
            "type": "free_text",
            "choices": []
        }

    monkeypatch.setattr(backend_app, "ai_chat_turn", fake_ai_chat_turn)

    client.post("/api/draft/start", json={
        "user_id": 10,
        "table": 1
    })

    res = client.post("/api/ai/chat", json={
        "user_id": 10,
        "title": "Internett virker ikke",
        "description": "Jeg kommer ikke på nett"
    })

    assert res.status_code == 200
    data = res.get_json()

    assert data["done"] is False
    assert data["question"] == "Når startet problemet?"
    assert data["type"] == "free_text"
    assert data["choices"] == []

    conn = backend_app.get_conn()
    row = conn.execute(
        "SELECT ai_questions_json, draft_title, draft_description FROM ticket_drafts WHERE user_id = ?",
        (10,)
    ).fetchone()
    conn.close()

    assert row is not None
    assert row["draft_title"] == "Internett virker ikke"
    assert "Jeg kommer ikke på nett" in row["draft_description"]

    questions = json.loads(row["ai_questions_json"])
    assert questions[0]["question"] == "Når startet problemet?"



def test_ai_chat_done_true(client, monkeypatch):
    def fake_ai_chat_turn(title, description, conversation, questions_asked):
        return {
            "done": True,
            "question": None,
            "type": None,
            "choices": []
        }

    monkeypatch.setattr(backend_app, "ai_chat_turn", fake_ai_chat_turn)

    client.post("/api/draft/start", json={
        "user_id": 11,
        "table": 1
    })

    res = client.post("/api/ai/chat", json={
        "user_id": 11,
        "title": "WiFi nede",
        "description": "Kommer ikke på nettet"
    })

    assert res.status_code == 200
    data = res.get_json()
    assert data["done"] is True


def test_ai_finalize_creates_improved_ticket(client, monkeypatch):
    def fake_ai_chat_turn(title, description, conversation, questions_asked):
        return {
            "done": True,
            "question": None,
            "type": None,
            "choices": []
        }

    def fake_improve_ticket_description(title, original_description, answers):
        return {
            "improved_description": (
                "Brukeren mistet nettverkstilgang på kontoret i dag tidlig. "
                "Har forsøkt å koble til på nytt uten hell."
            ),
            "category_guess": "network",
            "urgency_guess": "medium",
            "missing_info": []
        }

    monkeypatch.setattr(backend_app, "ai_chat_turn", fake_ai_chat_turn)
    monkeypatch.setattr(
        backend_app,
        "improve_ticket_description",
        fake_improve_ticket_description
    )

    client.post("/api/draft/start", json={
        "user_id": 12,
        "table": 2
    })

    chat_res = client.post("/api/ai/chat", json={
        "user_id": 12,
        "title": "Nettverksproblem",
        "description": "Internett virker ikke"
    })
    assert chat_res.status_code == 200
    assert chat_res.get_json()["done"] is True

    res = client.post("/api/ai/finalize", json={
        "user_id": 12
    })

    assert res.status_code == 201
    data = res.get_json()

    assert data["user_id"] == 12
    assert data["log_table"] == 2
    assert data["final"]["category_guess"] == "network"
    assert data["final"]["urgency_guess"] == "medium"

    conn = backend_app.get_conn()
    row = conn.execute(
        "SELECT * FROM tickets_2 WHERE user_id = ?",
        (12,)
    ).fetchone()

    draft_row = conn.execute(
        "SELECT state, submitted_at, ai_answers_json FROM ticket_drafts WHERE user_id = ?",
        (12,)
    ).fetchone()
    conn.close()

    assert row is not None
    assert row["ai_used"] == 1
    assert "Brukeren mistet nettverkstilgang" in row["description"]

    assert draft_row is not None
    assert draft_row["state"] == "submitted"
    assert draft_row["submitted_at"] is not None


def test_ai_chat_handles_ai_failure(client, monkeypatch):
    def fake_ai_chat_turn(title, description, conversation, questions_asked):
        raise RuntimeError("AI service failed")

    monkeypatch.setattr(backend_app, "ai_chat_turn", fake_ai_chat_turn)

    client.post("/api/draft/start", json={
        "user_id": 13,
        "table": 1
    })

    res = client.post("/api/ai/chat", json={
        "user_id": 13,
        "title": "Problem",
        "description": "Noe virker ikke"
    })

    assert res.status_code == 502
    data = res.get_json()
    assert "error" in data