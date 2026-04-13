import os
import tempfile
import pytest
import backend.app as backend_app 


#------Helpfunction: creating user in test-DB---------#

def create_user(user_id, username):
    conn = backend_app.get_conn()
    conn.execute(
        "INSERT INTO users (id, username) VALUES (?, ?)",
        (user_id, username)
    )
    conn.commit()
    conn.close()

#-creating test enviornment-#
@pytest.fixture
def client(monkeypatch):
    temp_db=tempfile.NamedTemporaryFile(delete=False)
    monkeypatch.setattr(backend_app,"DB_PATH", temp_db.name)
    backend_app.init_db()
    with backend_app.app.test_client() as client:
        yield client

    os.unlink(temp_db.name) 