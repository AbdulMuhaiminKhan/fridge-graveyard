"""Fridge Graveyard backend (FastAPI + SQLite).

Serves the frontend at / and stores the app state in a SQLite database
instead of the browser's localStorage.

Run:  uvicorn backend.main:app --reload   (from the project root)
"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND = BASE_DIR / "frontend" / "index.html"
DB_PATH = BASE_DIR / "backend" / "fridge.db"

app = FastAPI(title="Fridge Graveyard API")


@contextmanager
def get_db() -> Iterator[sqlite3.Connection]:
    """Open a connection, commit on success / roll back on error, and ALWAYS close it.

    sqlite3's own `with conn:` only commits or rolls back - it never closes the
    connection, which previously leaked one connection per request.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def validate_state(state: Dict[str, Any]) -> None:
    """Reject structurally invalid state before it is persisted."""
    items = state.get("items")
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="state.items must be a list")
    if not all(isinstance(i, dict) for i in items):
        raise HTTPException(status_code=400, detail="every entry in state.items must be an object")

    shopping = state.get("shopping")
    if shopping is not None:
        if not isinstance(shopping, list):
            raise HTTPException(status_code=400, detail="state.shopping must be a list")
        if not all(isinstance(s, dict) for s in shopping):
            raise HTTPException(status_code=400, detail="every entry in state.shopping must be an object")

    log = state.get("log")
    if log is not None and not isinstance(log, list):
        raise HTTPException(status_code=400, detail="state.log must be a list")

    stats = state.get("stats")
    if stats is not None:
        if not isinstance(stats, dict):
            raise HTTPException(status_code=400, detail="state.stats must be an object")
        for key, value in stats.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise HTTPException(status_code=400, detail=f"state.stats.{key} must be a number")


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
    validate_state(state)
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
