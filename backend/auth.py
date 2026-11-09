"""Accounts and sessions for Fridge Graveyard.

Passwords are never stored. We keep a salted PBKDF2-SHA256 hash (Python standard library,
no extra dependencies) and compare in constant time. Logging in gives the browser a random
session token, sent back as `Authorization: Bearer <token>` on every request.
"""
import hashlib
import hmac
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException

PBKDF2_ITERATIONS = 200_000
SESSION_DAYS = 30
USERNAME_RE = re.compile(r"^[a-z0-9_.]{3,20}$")


def hash_password(password: str, salt: Optional[bytes] = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt_hex, digest_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations))
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def clean_username(raw: str) -> str:
    username = (raw or "").strip().lower()
    if not USERNAME_RE.match(username):
        raise HTTPException(
            status_code=400,
            detail="Usernames are 3-20 characters: lowercase letters, numbers, dots or underscores.",
        )
    return username


def check_password(password: str) -> None:
    if len(password or "") < 6:
        raise HTTPException(status_code=400, detail="Passwords need at least 6 characters.")
    if len(password) > 200:
        raise HTTPException(status_code=400, detail="That password is too long.")


def clean_display_name(raw: str, fallback: str) -> str:
    name = re.sub(r"\s+", " ", (raw or "").strip())[:40]
    return name or fallback


def create_session(conn: sqlite3.Connection, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    conn.execute(
        "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (token, user_id, now.isoformat(), (now + timedelta(days=SESSION_DAYS)).isoformat()),
    )
    return token


def bearer_token(authorization: Optional[str]) -> Optional[str]:
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        return token or None
    return None


def user_for_token(conn: sqlite3.Connection, token: Optional[str]) -> Optional[sqlite3.Row]:
    if not token:
        return None
    row = conn.execute(
        """SELECT users.* , sessions.expires_at FROM sessions
           JOIN users ON users.id = sessions.user_id WHERE sessions.token = ?""",
        (token,),
    ).fetchone()
    if row is None:
        return None
    if row["expires_at"] < datetime.now(timezone.utc).isoformat():
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        return None
    return row
