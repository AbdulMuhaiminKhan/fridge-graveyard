"""Fridge Graveyard backend (FastAPI + SQLite).

Serves the frontend at / and stores app state in a SQLite database.

Data is stored per *household*. A household is identified by a short join code
(for example "K7QM2P"). Every browser gets its own household on first visit, so
visitors never see each other's fridge. Flatmates can share one fridge by
entering the same code on their phones ("Household syncing").

The browser sends its code in the `X-Household` header on every state request.
Saves carry the version they were based on (`X-Base-Version`), so if two people
in the same household edit at once, the later save is rejected with 409 instead
of silently overwriting the other person's change.

Run:  uvicorn backend.main:app --reload   (from the project root)
"""
import json
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND = BASE_DIR / "frontend" / "index.html"
DB_PATH = Path(os.environ.get("FRIDGE_DB_PATH", BASE_DIR / "backend" / "fridge.db"))

# Join codes avoid look-alike characters (0/O, 1/I/L) so they are easy to read aloud.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 6
CODE_RE = re.compile(rf"^[{CODE_ALPHABET}]{{{CODE_LENGTH}}}$")
MAX_STATE_BYTES = 2_000_000  # a fridge, list and log are a few KB; this stops abuse

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


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- validation

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
    if log is not None:
        if not isinstance(log, list):
            raise HTTPException(status_code=400, detail="state.log must be a list")
        if not all(isinstance(e, dict) for e in log):
            raise HTTPException(status_code=400, detail="every entry in state.log must be an object")

    stats = state.get("stats")
    if stats is not None:
        if not isinstance(stats, dict):
            raise HTTPException(status_code=400, detail="state.stats must be an object")
        for key, value in stats.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise HTTPException(status_code=400, detail=f"state.stats.{key} must be a number")

    goals = state.get("goals")
    if goals is not None and not isinstance(goals, dict):
        raise HTTPException(status_code=400, detail="state.goals must be an object")

    if len(json.dumps(state)) > MAX_STATE_BYTES:
        raise HTTPException(status_code=413, detail="state is too large")


def normalize_code(raw: Optional[str]) -> str:
    """Accept codes typed with spaces, dashes or lowercase ("k7q-m2p")."""
    code = re.sub(r"[\s-]", "", raw or "").upper()
    if not CODE_RE.match(code):
        raise HTTPException(status_code=400, detail="A valid 6-character household code is required")
    return code


# --------------------------------------------------------------------------- database

def init_db() -> None:
    with get_db() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS households (
                   code TEXT PRIMARY KEY,
                   data TEXT,
                   version INTEGER NOT NULL DEFAULT 0,
                   created_at TEXT NOT NULL,
                   updated_at TEXT NOT NULL
               )"""
        )
        conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        # Older versions kept one shared state row. Keep the table so that data can be
        # handed to the first household created after upgrading (see create_household).
        conn.execute(
            """CREATE TABLE IF NOT EXISTS app_state (
                   id INTEGER PRIMARY KEY CHECK (id = 1),
                   data TEXT NOT NULL,
                   updated_at TEXT NOT NULL
               )"""
        )


init_db()


def new_code(conn: sqlite3.Connection) -> str:
    while True:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        if not conn.execute("SELECT 1 FROM households WHERE code = ?", (code,)).fetchone():
            return code


def get_household(conn: sqlite3.Connection, code: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM households WHERE code = ?", (code,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Household not found")
    return row


def load_state(code: str) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        row = get_household(conn, code)
    return json.loads(row["data"]) if row["data"] else None


# --------------------------------------------------------------------------- routes

@app.get("/")
def index():
    return FileResponse(FRONTEND)


@app.post("/api/households", status_code=201)
def create_household():
    """Create a new household and return its join code.

    The first household created after upgrading from the old single-fridge version
    inherits that saved fridge, so nobody loses their existing data.
    """
    now = now_iso()
    with get_db() as conn:
        code = new_code(conn)
        data = None
        claimed = conn.execute("SELECT value FROM meta WHERE key = 'legacy_claimed'").fetchone()
        legacy = conn.execute("SELECT data FROM app_state WHERE id = 1").fetchone()
        if legacy and not claimed:
            data = legacy["data"]
            conn.execute("INSERT INTO meta (key, value) VALUES ('legacy_claimed', ?)", (code,))
        conn.execute(
            "INSERT INTO households (code, data, version, created_at, updated_at) VALUES (?, ?, 0, ?, ?)",
            (code, data, now, now),
        )
    return {"code": code}


@app.get("/api/households/{code}")
def check_household(code: str):
    """Used by "Join a household" to check a code before switching to it."""
    code = normalize_code(code)
    with get_db() as conn:
        row = get_household(conn, code)
    state = json.loads(row["data"]) if row["data"] else None
    return {
        "code": code,
        "items": len(state.get("items", [])) if state else 0,
        "updated_at": row["updated_at"],
    }


@app.get("/api/state")
def get_state(x_household: Optional[str] = Header(default=None)):
    """The household's whole app state (null if nothing saved yet) and its version."""
    code = normalize_code(x_household)
    with get_db() as conn:
        row = get_household(conn, code)
    return {
        "state": json.loads(row["data"]) if row["data"] else None,
        "version": row["version"],
        "updated_at": row["updated_at"],
    }


@app.put("/api/state")
def save_state(
    state: Dict[str, Any],
    x_household: Optional[str] = Header(default=None),
    x_base_version: Optional[int] = Header(default=None),
):
    code = normalize_code(x_household)
    validate_state(state)
    now = now_iso()
    with get_db() as conn:
        row = get_household(conn, code)
        if x_base_version is not None and x_base_version != row["version"]:
            # Someone else in the household saved first. Tell the browser to reload.
            return JSONResponse(
                status_code=409,
                content={"detail": "This household changed on another device", "version": row["version"]},
            )
        version = row["version"] + 1
        conn.execute(
            "UPDATE households SET data = ?, version = ?, updated_at = ? WHERE code = ?",
            (json.dumps(state), version, now, code),
        )
    return {"ok": True, "version": version, "updated_at": now}


@app.delete("/api/state")
def reset_state(x_household: Optional[str] = Header(default=None)):
    """Wipe this household's data only (the app then reloads demo data)."""
    code = normalize_code(x_household)
    now = now_iso()
    with get_db() as conn:
        row = get_household(conn, code)
        conn.execute(
            "UPDATE households SET data = NULL, version = ?, updated_at = ? WHERE code = ?",
            (row["version"] + 1, now, code),
        )
    return {"ok": True}


@app.get("/api/items")
def list_items(x_household: Optional[str] = Header(default=None)):
    """Read-only view of the household's fridge items."""
    state = load_state(normalize_code(x_household)) or {}
    return state.get("items", [])


@app.get("/api/shopping")
def list_shopping(x_household: Optional[str] = Header(default=None)):
    state = load_state(normalize_code(x_household)) or {}
    return state.get("shopping", [])


@app.get("/api/stats")
def get_stats(x_household: Optional[str] = Header(default=None)):
    state = load_state(normalize_code(x_household)) or {}
    return state.get("stats", {})


@app.get("/api/stats/members")
def member_stats(x_household: Optional[str] = Header(default=None)):
    """Who-ate-what: rescued and wasted counts per household member, from the case log."""
    state = load_state(normalize_code(x_household)) or {}
    board: Dict[str, Dict[str, Any]] = {}
    for entry in state.get("log", []):
        if entry.get("demo"):
            continue
        name = str(entry.get("by") or "Someone").strip()[:40] or "Someone"
        row = board.setdefault(name, {"name": name, "rescued": 0, "wasted": 0})
        if entry.get("outcome") == "eaten":
            row["rescued"] += 1
        elif entry.get("outcome") == "wasted":
            row["wasted"] += 1
    return sorted(board.values(), key=lambda r: (-r["rescued"], r["wasted"], r["name"]))
