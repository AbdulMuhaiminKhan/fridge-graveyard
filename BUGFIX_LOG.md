# Bug Fix Log

Each entry: ID, date, severity, symptom, root cause, fix, and how it was verified.

---

## BUG-001 — SQLite connections were never closed

- **Date fixed:** 2026-11-07
- **Severity:** High
- **File:** `backend/main.py` (`get_db`)
- **Symptom:** Every request (`GET`/`PUT`/`DELETE`) left an open SQLite connection behind.
  An automated test counted 37 unclosed connections (`ResourceWarning: unclosed database`)
  after a short run. On a long-running server this leaks file handles and can cause
  `database is locked` errors.
- **Root cause:** Code used `with get_db() as conn:`. For `sqlite3.Connection`, the
  context manager only **commits / rolls back** the transaction — it does **not** close
  the connection.
- **Fix:** `get_db()` is now a `@contextmanager` that opens the connection, wraps it in
  `with conn:` (commit on success, rollback on error), and always calls `conn.close()`
  in a `finally` block. Call sites are unchanged.
- **Verification:** `test_connections_are_closed` now passes (0 `ResourceWarning`s).

---

## BUG-002 — Nested app state was not validated

- **Date fixed:** 2026-11-07
- **Severity:** Medium
- **File:** `backend/main.py` (`save_state` → new `validate_state`)
- **Symptom:** `PUT /api/state` only checked that `items` was a list. Payloads such as
  `{"items": [1, null, "junk"]}` or `{"items": [], "stats": "not-a-dict"}` were saved
  and later served back to the frontend, which would crash when rendering them.
- **Root cause:** Validation covered only the top-level type of `items`.
- **Fix:** Added `validate_state()`, called before anything is written. Returns `400` if:
  - `items` is not a list, or any entry is not an object
  - `shopping` (if present) is not a list, or any entry is not an object
  - `log` (if present) is not a list
  - `stats` (if present) is not an object, or any value is not a number (booleans rejected)
  
  Optional fields may still be omitted, and the frontend's real state shape
  (`version`, `nextId`, `items`, `shopping`, `stats`, `log`, `demo`) is accepted unchanged.
- **Verification:** `test_bug2_bad_nested_state_rejected` (7 bad payloads → `400`,
  nothing persisted) and `test_bug2_frontend_shape_accepted` pass.

---

## BUG-003 — Frontend treated rejected saves as successful

- **Date fixed:** 2026-11-07
- **Severity:** Medium
- **File:** `frontend/index.html` (`saveState`)
- **Symptom:** If the backend rejected a save (e.g. `400` from the new validation, or a `500`),
  the user saw no warning and assumed their changes were stored.
- **Root cause:** `fetch()` only rejects on network errors; HTTP error statuses resolve
  normally, and `saveState` never checked `res.ok`.
- **Fix:** `saveState` now throws when `!res.ok`, so the existing `.catch(...)` toast
  ("Could not reach the server. Changes not saved.") fires for HTTP errors too.
- **Verification:** `test_frontend_save_checks_status`.

---

## BUG-004 — Frontend page download dropped on a real server (old dependencies)

- **Date fixed:** 2026-11-07
- **Severity:** High (users could get a blank/broken page)
- **File:** `requirements.txt`
- **Symptom:** Against a real Uvicorn server, `GET /` delivered the full 82 KB page the
  first time, then repeat requests were cut off mid-download with
  `ConnectionResetError [WinError 10054]`. API (JSON) endpoints were unaffected, and the
  in-memory `TestClient` tests did not catch it.
- **Root cause:** Bug in the pinned `fastapi==0.115.0` / Starlette / `uvicorn==0.30.6`
  stack when streaming a `FileResponse` (on Windows, Python 3.14). The app code was not at fault:
  the identical code passed every check after upgrading only the libraries.
- **Fix:** Upgraded to `fastapi==0.162.0` (Starlette 1.10.0) and `uvicorn==0.54.0`.
  No code changes were needed.
- **Verification:** the live end-to-end script (5× `GET /` full 82,285 bytes, plus PUT/GET/DELETE
  and validation through the real server) now gives **ALL PASS**. Full 35-test suite passes on the new versions.
