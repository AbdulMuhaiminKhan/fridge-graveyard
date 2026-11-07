"""API tests for the Fridge Graveyard backend.

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


def new_household(client):
    res = client.post("/api/households")
    assert res.status_code == 201
    return res.json()["code"]


def h(code, version=None):
    headers = {"X-Household": code}
    if version is not None:
        headers["X-Base-Version"] = str(version)
    return headers


def test_new_household_starts_empty(client):
    code = new_household(client)
    assert len(code) == 6
    res = client.get("/api/state", headers=h(code))
    assert res.status_code == 200
    assert res.json()["state"] is None
    assert res.json()["version"] == 0


def test_save_and_load_round_trip(client):
    code = new_household(client)
    res = client.put("/api/state", json=SAMPLE, headers=h(code, 0))
    assert res.status_code == 200
    assert res.json()["version"] == 1
    body = client.get("/api/state", headers=h(code)).json()
    assert body["state"]["items"][0]["name"] == "Milk"
    assert body["version"] == 1


def test_households_are_isolated(client):
    a, b = new_household(client), new_household(client)
    client.put("/api/state", json=SAMPLE, headers=h(a))
    assert client.get("/api/state", headers=h(b)).json()["state"] is None


def test_missing_or_bad_code_is_rejected(client):
    assert client.get("/api/state").status_code == 400
    assert client.get("/api/state", headers=h("nope")).status_code == 400
    assert client.get("/api/state", headers=h("AAAAAA")).status_code == 404


def test_codes_are_normalized(client):
    code = new_household(client)
    messy = f"{code[:3].lower()}-{code[3:].lower()}"
    res = client.get(f"/api/households/{messy}")
    assert res.status_code == 200
    assert res.json()["code"] == code


def test_join_check_reports_item_count(client):
    code = new_household(client)
    client.put("/api/state", json=SAMPLE, headers=h(code))
    assert client.get(f"/api/households/{code}").json()["items"] == 1
    assert client.get("/api/households/ZZZZZZ").status_code == 404


def test_stale_save_gets_conflict(client):
    code = new_household(client)
    assert client.put("/api/state", json=SAMPLE, headers=h(code, 0)).status_code == 200
    # A second phone still thinks the household is at version 0.
    res = client.put("/api/state", json=SAMPLE, headers=h(code, 0))
    assert res.status_code == 409
    assert res.json()["version"] == 1


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
    code = new_household(client)
    assert client.put("/api/state", json=bad, headers=h(code)).status_code == 400


def test_oversized_state_is_rejected(client):
    code = new_household(client)
    huge = {**SAMPLE, "items": [{"name": "x" * 1000} for _ in range(3000)]}
    assert client.put("/api/state", json=huge, headers=h(code)).status_code == 413


def test_reset_only_affects_own_household(client):
    a, b = new_household(client), new_household(client)
    client.put("/api/state", json=SAMPLE, headers=h(a))
    client.put("/api/state", json=SAMPLE, headers=h(b))
    assert client.delete("/api/state", headers=h(a)).status_code == 200
    assert client.get("/api/state", headers=h(a)).json()["state"] is None
    assert client.get("/api/state", headers=h(b)).json()["state"] is not None


def test_member_stats_count_who_ate_what(client):
    code = new_household(client)
    state = {**SAMPLE, "log": [
        {"name": "Rice", "outcome": "eaten", "by": "Abdul"},
        {"name": "Milk", "outcome": "eaten", "by": "Abdul"},
        {"name": "Bread", "outcome": "wasted", "by": "Minha"},
        {"name": "Old demo", "outcome": "eaten", "by": "Abdul", "demo": True},
        {"name": "Eggs", "outcome": "eaten"},
    ]}
    client.put("/api/state", json=state, headers=h(code))
    board = client.get("/api/stats/members", headers=h(code)).json()
    assert board[0] == {"name": "Abdul", "rescued": 2, "wasted": 0}
    assert {"name": "Minha", "rescued": 0, "wasted": 1} in board
    assert {"name": "Someone", "rescued": 1, "wasted": 0} in board


def test_old_single_fridge_data_moves_to_first_household(make_client):
    client, db = make_client()
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO app_state (id, data, updated_at) VALUES (1, ?, 'then')",
            (json.dumps(SAMPLE),),
        )
    first = new_household(client)
    second = new_household(client)
    assert client.get("/api/state", headers=h(first)).json()["state"]["items"][0]["name"] == "Milk"
    assert client.get("/api/state", headers=h(second)).json()["state"] is None


def test_read_only_endpoints_use_the_household(client):
    code = new_household(client)
    client.put("/api/state", json={**SAMPLE, "shopping": [{"name": "Eggs"}]}, headers=h(code))
    assert client.get("/api/items", headers=h(code)).json()[0]["name"] == "Milk"
    assert client.get("/api/shopping", headers=h(code)).json()[0]["name"] == "Eggs"
    assert client.get("/api/stats", headers=h(code)).json()["rescued"] == 0


def test_frontend_is_served(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "Fridge Graveyard" in res.text
