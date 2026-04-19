"""
test_app.py

Enhetstester for IT helpdesk ticket backend (app.py).

Kjør med:  pytest test_app.py -v
"""

import json
import pytest
from unittest.mock import patch, MagicMock

from app import app, init_db, get_conn


# ===========================================================================
# FIXTURES
# ===========================================================================

@pytest.fixture
def client(tmp_path, monkeypatch):
    """
    Setter opp en isolert testinstans:
    - Peker databasen mot en midlertidig fil (ikke tickets.db på disk).
    - Kjører init_db() slik at tabellene finnes.
    - Gir tilbake Flask test-klienten.
    """
    db_file = str(tmp_path / "test_tickets.db")
    monkeypatch.setattr("app.DB_PATH", db_file)
    app.config["TESTING"] = True
    init_db()
    with app.test_client() as c:
        yield c


def make_draft(client, user_id=1, table=1):
    """Hjelpefunksjon: starter en draft og returnerer responsen."""
    return client.post("/api/draft/start", json={"user_id": user_id, "table": table})


# ===========================================================================
# /api/ping
# ===========================================================================

class TestPing:
    def test_returns_200(self, client):
        res = client.get("/api/ping")
        assert res.status_code == 200

    def test_returns_ok_true(self, client):
        data = res = client.get("/api/ping").get_json()
        assert data["ok"] is True


# ===========================================================================
# /api/draft/start
# ===========================================================================

class TestStartDraft:

    def test_valid_request_returns_200(self, client):
        res = make_draft(client, user_id=1, table=1)
        assert res.status_code == 200

    def test_response_contains_user_id(self, client):
        res = make_draft(client, user_id=5, table=2)
        assert res.get_json()["user_id"] == 5

    def test_user_id_too_high_returns_400(self, client):
        res = client.post("/api/draft/start", json={"user_id": 999, "table": 1})
        assert res.status_code == 400

    def test_user_id_zero_returns_400(self, client):
        res = client.post("/api/draft/start", json={"user_id": 0, "table": 1})
        assert res.status_code == 400

    def test_user_id_string_returns_400(self, client):
        res = client.post("/api/draft/start", json={"user_id": "abc", "table": 1})
        assert res.status_code == 400

    def test_table_out_of_range_returns_400(self, client):
        res = client.post("/api/draft/start", json={"user_id": 1, "table": 6})
        assert res.status_code == 400

    def test_table_zero_returns_400(self, client):
        res = client.post("/api/draft/start", json={"user_id": 1, "table": 0})
        assert res.status_code == 400

    def test_starting_draft_twice_resets_state(self, client):
        """En ny draft skal overskrive den gamle — ikke gi feil."""
        make_draft(client, user_id=10, table=1)
        res = make_draft(client, user_id=10, table=2)
        assert res.status_code == 200


# ===========================================================================
# /api/tickets  (POST — submit uten AI)
# ===========================================================================

class TestCreateTicket:

    def test_valid_ticket_returns_201(self, client):
        make_draft(client, user_id=2, table=1)
        res = client.post("/api/tickets", json={
            "user_id": 2,
            "title": "Skjermen slutter å virke",
            "description": "Skjermen ble svart etter Windows-oppdatering i dag tidlig."
        })
        assert res.status_code == 201

    def test_response_contains_user_id(self, client):
        make_draft(client, user_id=3, table=1)
        res = client.post("/api/tickets", json={
            "user_id": 3,
            "title": "VPN fungerer ikke",
            "description": "Kan ikke koble til VPN siden i går."
        })
        data = res.get_json()
        assert data["user_id"] == 3

    def test_response_contains_log_table(self, client):
        make_draft(client, user_id=4, table=3)
        res = client.post("/api/tickets", json={
            "user_id": 4,
            "title": "Trenger ny mus",
            "description": "Musen er ødelagt."
        })
        assert res.get_json()["log_table"] == 3

    def test_without_draft_returns_400(self, client):
        """Skal ikke kunne sende ticket uten aktiv draft."""
        res = client.post("/api/tickets", json={
            "user_id": 50,
            "title": "Test",
            "description": "Ingen draft her."
        })
        assert res.status_code == 400

    def test_missing_title_returns_400(self, client):
        make_draft(client, user_id=6, table=1)
        res = client.post("/api/tickets", json={
            "user_id": 6,
            "title": "",
            "description": "Beskrivelse er til stede."
        })
        assert res.status_code == 400

    def test_missing_description_returns_400(self, client):
        make_draft(client, user_id=7, table=1)
        res = client.post("/api/tickets", json={
            "user_id": 7,
            "title": "Tittel er til stede",
            "description": ""
        })
        assert res.status_code == 400

    def test_invalid_user_id_returns_400(self, client):
        res = client.post("/api/tickets", json={
            "user_id": 200,
            "title": "Test",
            "description": "Beskrivelse"
        })
        assert res.status_code == 400

    def test_ticket_is_stored_in_database(self, client, tmp_path, monkeypatch):
        """Verifiserer at ticketen faktisk landes i databasen."""
        db_file = str(tmp_path / "test_tickets.db")
        monkeypatch.setattr("app.DB_PATH", db_file)
        init_db()

        with app.test_client() as c:
            c.post("/api/draft/start", json={"user_id": 8, "table": 2})
            c.post("/api/tickets", json={
                "user_id": 8,
                "title": "Ingen lyd",
                "description": "Lyden forsvant etter oppdatering."
            })

        conn = get_conn()
        row = conn.execute("SELECT * FROM tickets_2 WHERE user_id = 8").fetchone()
        conn.close()
        assert row is not None
        assert row["title"] == "Ingen lyd"


# ===========================================================================
# /api/tickets  (GET — list tickets)
# ===========================================================================

class TestListTickets:

    def test_returns_200(self, client):
        res = client.get("/api/tickets")
        assert res.status_code == 200

    def test_returns_list(self, client):
        res = client.get("/api/tickets")
        assert isinstance(res.get_json(), list)

    def test_submitted_ticket_appears_in_list(self, client):
        make_draft(client, user_id=9, table=1)
        client.post("/api/tickets", json={
            "user_id": 9,
            "title": "Printer offline",
            "description": "Printeren er offline siden mandag."
        })
        tickets = client.get("/api/tickets").get_json()
        titles = [t["title"] for t in tickets]
        assert "Printer offline" in titles


# ===========================================================================
# /api/ai/chat  (mockes — ingen ekte OpenAI-kall)
# ===========================================================================

class TestAiChat:

    @patch("app.ai_chat_turn")
    def test_first_call_returns_question(self, mock_ai, client):
        """AI returnerer et spørsmål — sjekker at endepunktet sender det videre."""
        mock_ai.return_value = {
            "done": False,
            "question": "Når startet problemet?",
            "type": "multiple_choice",
            "choices": ["I dag", "I går", "Denne uken"]
        }
        make_draft(client, user_id=11, table=1)
        res = client.post("/api/ai/chat", json={
            "user_id": 11,
            "title": "Noe er ødelagt",
            "description": "Ting fungerer ikke."
        })
        assert res.status_code == 200
        data = res.get_json()
        assert data["done"] is False
        assert data["question"] == "Når startet problemet?"
        assert "I dag" in data["choices"]

    @patch("app.ai_chat_turn")
    def test_first_call_done_true(self, mock_ai, client):
        """AI bestemmer at beskrivelsen er god nok med en gang."""
        mock_ai.return_value = {"done": True, "question": None, "type": None, "choices": []}
        make_draft(client, user_id=12, table=1)
        res = client.post("/api/ai/chat", json={
            "user_id": 12,
            "title": "Detaljert tittel",
            "description": "Nettverket falt ut kl 09:00 i dag. Har prøvd å starte ruteren."
        })
        assert res.status_code == 200
        assert res.get_json()["done"] is True

    def test_no_draft_returns_400(self, client):
        res = client.post("/api/ai/chat", json={
            "user_id": 60,
            "title": "Test",
            "description": "Ingen draft."
        })
        assert res.status_code == 400

    def test_invalid_user_id_returns_400(self, client):
        res = client.post("/api/ai/chat", json={
            "user_id": 100,
            "title": "Test",
            "description": "Beskrivelse"
        })
        assert res.status_code == 400

    @patch("app.ai_chat_turn")
    def test_subsequent_call_with_prior_answer(self, mock_ai, client):
        """Simulerer andre kall i konversasjonen (bruker svarer på spørsmål)."""
        mock_ai.return_value = {"done": True, "question": None, "type": None, "choices": []}

        make_draft(client, user_id=13, table=1)
        client.post("/api/ai/chat", json={
            "user_id": 13,
            "title": "Problem",
            "description": "Noe er galt."
        })
        mock_ai.return_value = {"done": True, "question": None, "type": None, "choices": []}
        res = client.post("/api/ai/chat", json={
            "user_id": 13,
            "prior_answer": "Det startet i dag tidlig."
        })
        assert res.status_code == 200


# ===========================================================================
# /api/ai/finalize  (mockes)
# ===========================================================================

class TestAiFinalize:

    @patch("app.improve_ticket_description")
    def test_finalize_returns_201(self, mock_improve, client):
        mock_improve.return_value = {
            "improved_description": "Forbedret beskrivelse her.",
            "category_guess": "network",
            "urgency_guess": "high",
            "missing_info": []
        }
        make_draft(client, user_id=20, table=1)
        # Legg inn draft-innhold direkte i DB
        conn = get_conn()
        conn.execute(
            "UPDATE ticket_drafts SET draft_title=?, draft_description=?, ai_questions_json=? WHERE user_id=?",
            ("VPN problem", "Kan ikke koble til VPN.", json.dumps([]), 20)
        )
        conn.commit()
        conn.close()

        res = client.post("/api/ai/finalize", json={"user_id": 20})
        assert res.status_code == 201

    @patch("app.improve_ticket_description")
    def test_finalize_response_has_final_key(self, mock_improve, client):
        mock_improve.return_value = {
            "improved_description": "Nettverksfeil på kontoret siden kl. 09:00.",
            "category_guess": "network",
            "urgency_guess": "medium",
            "missing_info": []
        }
        make_draft(client, user_id=21, table=1)
        conn = get_conn()
        conn.execute(
            "UPDATE ticket_drafts SET draft_title=?, draft_description=?, ai_questions_json=? WHERE user_id=?",
            ("Nettverk nede", "Internett fungerer ikke.", json.dumps([]), 21)
        )
        conn.commit()
        conn.close()

        data = client.post("/api/ai/finalize", json={"user_id": 21}).get_json()
        assert "final" in data
        assert data["final"]["urgency_guess"] == "medium"

    def test_finalize_without_draft_returns_400(self, client):
        res = client.post("/api/ai/finalize", json={"user_id": 70})
        assert res.status_code == 400

    def test_finalize_invalid_user_id_returns_400(self, client):
        res = client.post("/api/ai/finalize", json={"user_id": 999})
        assert res.status_code == 400