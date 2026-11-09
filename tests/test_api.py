"""API tests for the Fridge Graveyard backend: accounts, households, state and receipts.

Run from the project root:  pytest
Each test gets its own temporary SQLite database, so your real fridge.db is never touched.
"""
import importlib
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

SAMPLE = {
    "version": 1,
    "items": [{"id": 101, "name": "Milk"}],
    "shopping": [],
    "log": [],
    "stats": {"rescued": 0, "wasted": 0, "moneySaved": 0, "moneyWasted": 0},
}


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    """Return a factory so a test can seed the database before the app starts."""
    db = tmp_path / "test.db"
    monkeypatch.setenv("FRIDGE_DB_PATH", str(db))

    def factory():
        import backend.main as main
        importlib.reload(main)  # re-reads FRIDGE_DB_PATH and creates the tables
        return TestClient(main.app), db

    return factory


@pytest.fixture
def client(make_client):
    return make_client()[0]


def register(client, username, password="secret123", name="", code=None):
    body = {"username": username, "password": password, "display_name": name}
    if code:
        body["household_code"] = code
    res = client.post("/api/auth/register", json=body)
    assert res.status_code == 201, res.text
    return res.json()


def auth(account, version=None):
    headers = {"Authorization": f"Bearer {account['token']}"}
    if version is not None:
        headers["X-Base-Version"] = str(version)
    return headers


# ------------------------------------------------------------------ accounts

def test_register_creates_own_household(client):
    abdul = register(client, "abdul", name="Abdul")
    assert abdul["user"] == {"username": "abdul", "display_name": "Abdul"}
    assert len(abdul["household"]["code"]) == 6
    assert abdul["household"]["members"] == [{"username": "abdul", "display_name": "Abdul", "you": True}]
    res = client.get("/api/state", headers=auth(abdul))
    assert res.status_code == 200
    assert res.json()["state"] is None


def test_passwords_are_hashed_not_stored(make_client):
    client, db = make_client()
    register(client, "abdul", password="supersecret")
    with sqlite3.connect(db) as conn:
        stored = conn.execute("SELECT password_hash FROM users").fetchone()[0]
    assert "supersecret" not in stored
    assert stored.startswith("pbkdf2_sha256$")


def test_login_and_logout(client):
    register(client, "abdul", password="secret123")
    res = client.post("/api/auth/login", json={"username": "ABDUL ", "password": "secret123"})
    assert res.status_code == 200
    token = res.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/auth/me", headers=headers).json()["user"]["username"] == "abdul"
    assert client.post("/api/auth/logout", headers=headers).status_code == 200
    assert client.get("/api/auth/me", headers=headers).status_code == 401


def test_wrong_password_is_rejected(client):
    register(client, "abdul", password="secret123")
    res = client.post("/api/auth/login", json={"username": "abdul", "password": "nope-nope"})
    assert res.status_code == 401
    assert client.post("/api/auth/login", json={"username": "ghost", "password": "secret123"}).status_code == 401


@pytest.mark.parametrize("username,password", [("ab", "secret123"), ("bad name!", "secret123"), ("abdul", "123")])
def test_invalid_signups_are_rejected(client, username, password):
    res = client.post("/api/auth/register", json={"username": username, "password": password})
    assert res.status_code == 400


def test_usernames_are_unique(client):
    register(client, "abdul")
    res = client.post("/api/auth/register", json={"username": "Abdul", "password": "secret123"})
    assert res.status_code == 409


def test_state_requires_login(client):
    assert client.get("/api/state").status_code == 401
    assert client.put("/api/state", json=SAMPLE).status_code == 401
    assert client.get("/api/state", headers={"Authorization": "Bearer made-up"}).status_code == 401


# ------------------------------------------------------------------ households

def test_two_users_share_one_fridge(client):
    abdul = register(client, "abdul", name="Abdul")
    code = abdul["household"]["code"]
    minha = register(client, "minha", name="Minha", code=code.lower())
    assert minha["household"]["code"] == code
    assert [m["display_name"] for m in minha["household"]["members"]] == ["Abdul", "Minha"]

    client.put("/api/state", json=SAMPLE, headers=auth(abdul, 0))
    seen = client.get("/api/state", headers=auth(minha)).json()
    assert seen["state"]["items"][0]["name"] == "Milk"
    assert seen["updated_by"] == "Abdul"


def test_join_and_leave_household(client):
    abdul = register(client, "abdul", name="Abdul")
    minha = register(client, "minha", name="Minha")
    code = abdul["household"]["code"]
    client.put("/api/state", json=SAMPLE, headers=auth(abdul))

    preview = client.get(f"/api/households/{code}", headers=auth(minha)).json()
    assert preview == {"code": code, "items": 1, "members": ["Abdul"]}

    joined = client.post("/api/household/join", json={"code": code}, headers=auth(minha)).json()
    assert joined["household"]["code"] == code
    assert client.get("/api/state", headers=auth(minha)).json()["state"]["items"][0]["name"] == "Milk"

    left = client.post("/api/household/leave", headers=auth(minha)).json()
    assert left["household"]["code"] != code
    assert client.get("/api/state", headers=auth(minha)).json()["state"] is None
    # The shared household keeps its data for Abdul.
    assert client.get("/api/state", headers=auth(abdul)).json()["state"]["items"][0]["name"] == "Milk"


def test_unknown_or_bad_codes_are_rejected(client):
    abdul = register(client, "abdul")
    assert client.post("/api/household/join", json={"code": "AAAAAA"}, headers=auth(abdul)).status_code == 404
    assert client.post("/api/household/join", json={"code": "nope"}, headers=auth(abdul)).status_code == 400
    res = client.post("/api/auth/register", json={"username": "minha", "password": "secret123", "household_code": "AAAAAA"})
    assert res.status_code == 404


def test_households_are_isolated(client):
    abdul, other = register(client, "abdul"), register(client, "stranger")
    client.put("/api/state", json=SAMPLE, headers=auth(abdul))
    assert client.get("/api/state", headers=auth(other)).json()["state"] is None


def test_stale_save_gets_conflict(client):
    abdul = register(client, "abdul")
    minha = register(client, "minha", code=abdul["household"]["code"])
    assert client.put("/api/state", json=SAMPLE, headers=auth(abdul, 0)).status_code == 200
    # Minha's phone still thinks the household is at version 0.
    res = client.put("/api/state", json=SAMPLE, headers=auth(minha, 0))
    assert res.status_code == 409
    assert res.json()["version"] == 1


# ------------------------------------------------------------------ state

@pytest.mark.parametrize("bad", [
    {"items": {}},
    {"items": [1]},
    {"items": [], "shopping": "x"},
    {"items": [], "log": [1]},
    {"items": [], "stats": {"rescued": True}},
    {"items": [], "stats": {"rescued": "1"}},
    {"items": [], "goals": []},
    {},
])
def test_invalid_state_is_rejected(client, bad):
    abdul = register(client, "abdul")
    assert client.put("/api/state", json=bad, headers=auth(abdul)).status_code == 400


def test_oversized_state_is_rejected(client):
    abdul = register(client, "abdul")
    huge = {**SAMPLE, "items": [{"name": "x" * 1000} for _ in range(3000)]}
    assert client.put("/api/state", json=huge, headers=auth(abdul)).status_code == 413


def test_reset_only_affects_own_household(client):
    a, b = register(client, "abdul"), register(client, "minha")
    client.put("/api/state", json=SAMPLE, headers=auth(a))
    client.put("/api/state", json=SAMPLE, headers=auth(b))
    assert client.delete("/api/state", headers=auth(a)).status_code == 200
    assert client.get("/api/state", headers=auth(a)).json()["state"] is None
    assert client.get("/api/state", headers=auth(b)).json()["state"] is not None


def test_member_stats_count_who_ate_what(client):
    abdul = register(client, "abdul", name="Abdul")
    register(client, "minha", name="Minha", code=abdul["household"]["code"])
    register(client, "jaclyn", name="Jaclyn", code=abdul["household"]["code"])
    state = {**SAMPLE, "log": [
        {"name": "Rice", "outcome": "eaten", "by": "Abdul"},
        {"name": "Milk", "outcome": "eaten", "by": "Abdul"},
        {"name": "Bread", "outcome": "wasted", "by": "Minha"},
        {"name": "Old demo", "outcome": "eaten", "by": "Abdul", "demo": True},
    ]}
    client.put("/api/state", json=state, headers=auth(abdul))
    board = client.get("/api/stats/members", headers=auth(abdul)).json()
    assert board[0] == {"name": "Abdul", "rescued": 2, "wasted": 0}
    assert {"name": "Minha", "rescued": 0, "wasted": 1} in board
    assert {"name": "Jaclyn", "rescued": 0, "wasted": 0} in board   # members show up before closing a case


def test_old_single_fridge_data_moves_to_first_household(make_client):
    client, db = make_client()
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO app_state (id, data, updated_at) VALUES (1, ?, 'then')",
            (json.dumps(SAMPLE),),
        )
    first = register(client, "abdul")
    second = register(client, "minha")
    assert client.get("/api/state", headers=auth(first)).json()["state"]["items"][0]["name"] == "Milk"
    assert client.get("/api/state", headers=auth(second)).json()["state"] is None


def test_read_only_endpoints_use_the_household(client):
    abdul = register(client, "abdul")
    client.put("/api/state", json={**SAMPLE, "shopping": [{"name": "Eggs"}]}, headers=auth(abdul))
    assert client.get("/api/items", headers=auth(abdul)).json()[0]["name"] == "Milk"
    assert client.get("/api/shopping", headers=auth(abdul)).json()[0]["name"] == "Eggs"
    assert client.get("/api/stats", headers=auth(abdul)).json()["rescued"] == 0


def test_frontend_is_served(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "Fridge Graveyard" in res.text


# ------------------------------------------------------------------ receipts

def test_receipt_parse_endpoint(client):
    res = client.post("/api/receipts/parse", json={"text": "BABYSPINAT 125G 1,79 B\nPFAND 0,25\nSUMME 2,04"})
    assert res.status_code == 200
    body = res.json()
    assert [i["name"] for i in body["items"]] == ["Baby spinach"]
    assert body["items"][0]["suggested"] is True


def test_receipt_parse_rejects_empty_and_huge_text(client):
    assert client.post("/api/receipts/parse", json={"text": "   "}).status_code == 400
    assert client.post("/api/receipts/parse", json={"text": "x" * 20_001}).status_code == 422
