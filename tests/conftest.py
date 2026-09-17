import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db  # noqa: E402
from app.main import app  # noqa: E402

ADMIN_TOKEN = "test-token-abc123"


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Point the app at a fresh database for this test."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    db.init_db()
    return tmp_path


@pytest.fixture
def client(data_dir, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("DISCORD_INVITE_URL", "https://discord.gg/testinvite")
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def client_no_admin(data_dir, monkeypatch):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth():
    return {"Authorization": f"Bearer {ADMIN_TOKEN}"}


def rows(data_dir, sql, params=()):
    conn = db.connect()
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def start_session(client, discord="", headers=None):
    """Page one to the email step. This is what issues the session cookie."""
    return client.post("/email", data={"discord_username": discord}, headers=headers or {})


def walk(client, email, discord="", headers=None):
    """Walk the form the way an attendee does, then submit the email."""
    start_session(client, discord, headers)
    return client.post("/claim", data={"email": email}, headers=headers or {})
