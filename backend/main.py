"""Fridge Graveyard backend (FastAPI + SQLite).

Serves the frontend at / and stores app state in a SQLite database.

People sign in with a username and password (see backend/auth.py). Every account belongs to
one *household*, and the household owns the fridge, shopping list, goals and stats. Flatmates
share a fridge by joining the same household with its 6-character code (e.g. "K7QM2P");
everyone in it sees the same data, and changes reach the other phones within seconds.

Requests carry the session token in `Authorization: Bearer <token>`. Saves carry the version
they were based on (`X-Base-Version`), so if two people edit at the same moment the later save
is rejected with 409 instead of silently overwriting the other person's change.

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

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from backend import auth
from backend.receipts import parse_receipt

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
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(households)")}
        if "updated_by" not in columns:
            conn.execute("ALTER TABLE households ADD COLUMN updated_by TEXT")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS users (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   username TEXT NOT NULL UNIQUE,
                   display_name TEXT NOT NULL,
                   password_hash TEXT NOT NULL,
                   household_code TEXT NOT NULL REFERENCES households(code),
                   created_at TEXT NOT NULL
               )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS sessions (
                   token TEXT PRIMARY KEY,
                   user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                   created_at TEXT NOT NULL,
                   expires_at TEXT NOT NULL
               )"""
        )
        conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        # Older versions kept one shared state row. Keep the table so that data can be
        # handed to the first household created after upgrading (see make_household).
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


def make_household(conn: sqlite3.Connection) -> str:
    """Create an empty household. The very first one after upgrading from the old
    single-fridge version inherits that saved fridge, so nobody loses their data."""
    now = now_iso()
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
    return code


def members_of(conn: sqlite3.Connection, code: str):
    return conn.execute(
        "SELECT id, username, display_name FROM users WHERE household_code = ? ORDER BY created_at, id",
        (code,),
    ).fetchall()


def current_user(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    """FastAPI dependency: the signed-in user, or 401."""
    with get_db() as conn:
        user = auth.user_for_token(conn, auth.bearer_token(authorization))
    if user is None:
        raise HTTPException(status_code=401, detail="Please log in")
    return dict(user)


def account_payload(conn: sqlite3.Connection, user: Dict[str, Any]) -> Dict[str, Any]:
    code = user["household_code"]
    return {
        "user": {"username": user["username"], "display_name": user["display_name"]},
        "household": {
            "code": code,
            "members": [
                {"username": m["username"], "display_name": m["display_name"], "you": m["id"] == user["id"]}
                for m in members_of(conn, code)
            ],
        },
    }


# --------------------------------------------------------------------------- routes

@app.get("/")
def index():
    return FileResponse(FRONTEND)


class RegisterBody(BaseModel):
    username: str = Field(..., max_length=40)
    password: str = Field(..., max_length=200)
    display_name: str = Field("", max_length=60)
    household_code: Optional[str] = Field(None, max_length=12, description="Join an existing household")


class LoginBody(BaseModel):
    username: str = Field(..., max_length=40)
    password: str = Field(..., max_length=200)


@app.post("/api/auth/register", status_code=201)
def register(body: RegisterBody):
    """Create an account. Without a code you get your own new household; with a code you
    join that household straight away (e.g. a flatmate's fridge)."""
    username = auth.clean_username(body.username)
    auth.check_password(body.password)
    display_name = auth.clean_display_name(body.display_name, username.capitalize())
    with get_db() as conn:
        if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            raise HTTPException(status_code=409, detail="That username is taken. Try another one.")
        if body.household_code and body.household_code.strip():
            code = normalize_code(body.household_code)
            get_household(conn, code)
        else:
            code = make_household(conn)
        cur = conn.execute(
            "INSERT INTO users (username, display_name, password_hash, household_code, created_at) VALUES (?, ?, ?, ?, ?)",
            (username, display_name, auth.hash_password(body.password), code, now_iso()),
        )
        user = dict(conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone())
        token = auth.create_session(conn, user["id"])
        return {"token": token, **account_payload(conn, user)}


@app.post("/api/auth/login")
def login(body: LoginBody):
    username = (body.username or "").strip().lower()
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if row is None or not auth.verify_password(body.password, row["password_hash"]):
            raise HTTPException(status_code=401, detail="Wrong username or password.")
        user = dict(row)
        token = auth.create_session(conn, user["id"])
        return {"token": token, **account_payload(conn, user)}


@app.post("/api/auth/logout")
def logout(authorization: Optional[str] = Header(default=None)):
    token = auth.bearer_token(authorization)
    if token:
        with get_db() as conn:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
    return {"ok": True}


@app.get("/api/auth/me")
def me(user: Dict[str, Any] = Depends(current_user)):
    with get_db() as conn:
        return account_payload(conn, user)


class JoinBody(BaseModel):
    code: str = Field(..., max_length=12)


@app.get("/api/households/{code}")
def preview_household(code: str, user: Dict[str, Any] = Depends(current_user)):
    """Shown before joining: who is in that household and how full the fridge is."""
    code = normalize_code(code)
    with get_db() as conn:
        row = get_household(conn, code)
        names = [m["display_name"] for m in members_of(conn, code)]
    state = json.loads(row["data"]) if row["data"] else None
    return {"code": code, "items": len(state.get("items", [])) if state else 0, "members": names}


@app.post("/api/household/join")
def join_household(body: JoinBody, user: Dict[str, Any] = Depends(current_user)):
    code = normalize_code(body.code)
    with get_db() as conn:
        get_household(conn, code)
        conn.execute("UPDATE users SET household_code = ? WHERE id = ?", (code, user["id"]))
        user["household_code"] = code
        return account_payload(conn, user)


@app.post("/api/household/leave")
def leave_household(user: Dict[str, Any] = Depends(current_user)):
    """Start over with your own, new household (the shared one keeps its data)."""
    with get_db() as conn:
        code = make_household(conn)
        conn.execute("UPDATE users SET household_code = ? WHERE id = ?", (code, user["id"]))
        user["household_code"] = code
        return account_payload(conn, user)


def household_state(code: str) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        row = get_household(conn, code)
    return json.loads(row["data"]) if row["data"] else None


@app.get("/api/state")
def get_state(user: Dict[str, Any] = Depends(current_user)):
    """The household's whole app state (null if nothing saved yet), its version, and who saved last."""
    with get_db() as conn:
        row = get_household(conn, user["household_code"])
    return {
        "state": json.loads(row["data"]) if row["data"] else None,
        "version": row["version"],
        "updated_at": row["updated_at"],
        "updated_by": row["updated_by"],
    }


@app.put("/api/state")
def save_state(
    state: Dict[str, Any],
    user: Dict[str, Any] = Depends(current_user),
    x_base_version: Optional[int] = Header(default=None),
):
    validate_state(state)
    now = now_iso()
    code = user["household_code"]
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
            "UPDATE households SET data = ?, version = ?, updated_at = ?, updated_by = ? WHERE code = ?",
            (json.dumps(state), version, now, user["display_name"], code),
        )
    return {"ok": True, "version": version, "updated_at": now}


@app.delete("/api/state")
def reset_state(user: Dict[str, Any] = Depends(current_user)):
    """Wipe this household's data only (the app then reloads demo data)."""
    now = now_iso()
    with get_db() as conn:
        row = get_household(conn, user["household_code"])
        conn.execute(
            "UPDATE households SET data = NULL, version = ?, updated_at = ?, updated_by = ? WHERE code = ?",
            (row["version"] + 1, now, user["display_name"], user["household_code"]),
        )
    return {"ok": True}


@app.get("/api/items")
def list_items(user: Dict[str, Any] = Depends(current_user)):
    """Read-only view of the household's fridge items."""
    return (household_state(user["household_code"]) or {}).get("items", [])


@app.get("/api/shopping")
def list_shopping(user: Dict[str, Any] = Depends(current_user)):
    return (household_state(user["household_code"]) or {}).get("shopping", [])


@app.get("/api/stats")
def get_stats(user: Dict[str, Any] = Depends(current_user)):
    return (household_state(user["household_code"]) or {}).get("stats", {})


@app.get("/api/stats/members")
def member_stats(user: Dict[str, Any] = Depends(current_user)):
    """Who-ate-what: rescued and wasted counts per household member, from the case log.
    Every current member is listed, even before they have closed a case."""
    code = user["household_code"]
    state = household_state(code) or {}
    with get_db() as conn:
        members = [m["display_name"] for m in members_of(conn, code)]
    board: Dict[str, Dict[str, Any]] = {n: {"name": n, "rescued": 0, "wasted": 0} for n in members}
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


class ReceiptText(BaseModel):
    text: str = Field(..., max_length=20_000, description="Raw text read from the receipt photo")


@app.post("/api/receipts/parse")
def parse_receipt_text(body: ReceiptText):
    """Receipt scanner (Premium): turn text recognised from a receipt photo into fridge items.

    The photo itself never reaches the server; the browser reads it with Tesseract.js and
    only the text is sent here. Nothing is saved: the user reviews the items first.
    """
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="No text was found on the receipt")
    return parse_receipt(body.text)
