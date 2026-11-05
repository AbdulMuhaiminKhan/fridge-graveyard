"""Fridge Graveyard backend (FastAPI + SQLite).

Serves the frontend at / and stores the app state in a SQLite database
instead of the browser's localStorage.

Run:  uvicorn backend.main:app --reload   (from the project root)
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND = BASE_DIR / "frontend" / "index.html"
DB_PATH = BASE_DIR / "backend" / "fridge.db"

app = FastAPI(title="Fridge Graveyard API")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_db() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS app_state (
                   id INTEGER PRIMARY KEY CHECK (id = 1),
                   data TEXT NOT NULL,
                   updated_at TEXT NOT NULL
               )"""
        )


init_db()


def read_state():
    with get_db() as conn:
        row = conn.execute("SELECT data FROM app_state WHERE id = 1").fetchone()
    return json.loads(row["data"]) if row else None


@app.get("/")
def index():
    return FileResponse(FRONTEND)


@app.get("/api/state")
def get_state():
    """Whole app state, or null if nothing has been saved yet."""
    return {"state": read_state()}


@app.put("/api/state")
def save_state(state: Dict[str, Any]):
    if not isinstance(state.get("items"), list):
        raise HTTPException(status_code=400, detail="state.items must be a list")
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            """INSERT INTO app_state (id, data, updated_at) VALUES (1, ?, ?)
               ON CONFLICT(id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at""",
            (json.dumps(state), now),
        )
    return {"ok": True, "updated_at": now}


@app.delete("/api/state")
def reset_state():
    with get_db() as conn:
        conn.execute("DELETE FROM app_state")
    return {"ok": True}


@app.get("/api/items")
def list_items():
    """Read-only view of the fridge items."""
    state = read_state() or {}
    return state.get("items", [])


@app.get("/api/shopping")
def list_shopping():
    state = read_state() or {}
    return state.get("shopping", [])


@app.get("/api/stats")
def get_stats():
    state = read_state() or {}
    return state.get("stats", {})
