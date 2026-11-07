# Backend Mechanics Log

Audit of every backend mechanic in Fridge Graveyard (`backend/main.py` and its contract with
`frontend/index.html`). Each entry records **what it does**, **how it was checked**, the
**result**, and any **issue found** (fixed issues link to `BUGFIX_LOG.md`).

- **Audit date:** 2026-11-07
- **Environment:** Python 3.14, FastAPI 0.162.0 (upgraded from 0.115.0), Uvicorn 0.54.0 (from 0.30.6), SQLite (stdlib)
- **Test suite:** 35 automated tests (FastAPI `TestClient`) + 13-step live end-to-end check on a real Uvicorn server; isolated temp DBs

Status key: ✅ OK · 🔧 Fixed · ⚠️ Open (known limitation, not changed)

---

## Summary

| # | Mechanic | Status |
|---|---|---|
| M1 | DB connection lifecycle (`get_db`) | 🔧 BUG-001 |
| M2 | Schema & initialisation (`init_db`) | ✅ |
| M3 | Single-row state storage | ✅ |
| M4 | Read state (`read_state`) | ⚠️ corrupt row → 500 |
| M5 | `GET /` — serve frontend | 🔧 BUG-004 |
| M6 | `GET /api/state` | ✅ |
| M7 | `PUT /api/state` — save | ✅ |
| M8 | State validation (`validate_state`) | 🔧 BUG-002 |
| M9 | `DELETE /api/state` — reset | ✅ (unused by frontend) |
| M10 | `GET /api/items`, `/api/shopping`, `/api/stats` | ✅ |
| M11 | Transactions / rollback | ✅ |
| M12 | Concurrency | ✅ / ⚠️ last-write-wins |
| M13 | Error handling (404/405/422) | ✅ |
| M14 | Frontend load (`loadState`) | ⚠️ version mismatch overwrites |
| M15 | Frontend save (`saveState` + debounce) | 🔧 BUG-003 / ⚠️ unload loss |
| M16 | Security / limits | ⚠️ no auth, no size cap |

---

## M1 — DB connection lifecycle 🔧
- **What:** `get_db()` opens a SQLite connection to `backend/fridge.db` with `Row` factory.
- **Check:** `test_connections_are_closed` counts `ResourceWarning: unclosed database`.
- **Result:** Originally 37 leaked connections. Now a context manager that commits/rolls back
  and always closes. 0 leaks. → **BUG-001**.

## M2 — Schema & initialisation ✅
- **What:** `init_db()` runs at import; creates `app_state(id INTEGER PK CHECK(id=1), data TEXT, updated_at TEXT)`.
- **Check:** `test_init_db_idempotent`, `test_db_schema_and_singleton_row`.
- **Result:** `CREATE TABLE IF NOT EXISTS` is safe to repeat.
- **Note:** Runs as an import side effect (creates the DB file on import). Works, but a FastAPI
  startup/lifespan hook would be cleaner for testing.

## M3 — Single-row state storage ✅
- **What:** Entire app state stored as one JSON blob in row `id=1`; `CHECK (id = 1)` enforces it.
- **Check:** `test_singleton_check_constraint` (inserting id=2 raises `IntegrityError`);
  repeated saves leave exactly one row.
- **Result:** Works as designed.

## M4 — Read state ⚠️
- **What:** `read_state()` selects row 1 and `json.loads` it, or returns `None`.
- **Check:** `test_empty_state`, `test_roundtrip`, `test_corrupt_row_behaviour`.
- **Result:** Correct for valid data. **Open:** if the stored JSON is ever corrupt, every GET
  endpoint returns HTTP 500 and there is no recovery path except `DELETE /api/state`.
  Only reachable by editing the DB by hand, since writes always go through `json.dumps`.

## M5 — `GET /` serve frontend 🔧
- **What:** `FileResponse(frontend/index.html)`.
- **Check:** `test_index_serves_frontend` (in-memory) + live end-to-end script against a real
  Uvicorn server (5 repeated `GET /` requests).
- **Issue found:** On the original pinned versions, the first `GET /` succeeded but repeat
  requests were cut off mid-download (`ConnectionResetError`). The in-memory tests could not
  catch this. Same code passes after upgrading FastAPI/Uvicorn → **BUG-004** (fixed).
- **Result now:** full 82,285-byte body every time, `text/html; charset=utf-8`, ETag +
  Last-Modified headers present.

## M6 — `GET /api/state` ✅
- **What:** Returns `{"state": <object|null>}`.
- **Check:** empty → `{"state": null}`; after save → exact payload; Unicode/emoji preserved.

## M7 — `PUT /api/state` ✅
- **What:** Validates, then UPSERTs row 1 with JSON + UTC ISO timestamp; returns `{ok, updated_at}`.
- **Check:** `test_roundtrip`, `test_overwrite`, `test_put_returns_updated_at`,
  `test_large_payload` (20,000 items), timestamp is timezone-aware.
- **Result:** Works. Whole-state replace (see M12).

## M8 — State validation 🔧
- **What:** `validate_state()` checks `items`/`shopping` are lists of objects, `log` is a list,
  `stats` is an object of numbers. Non-object body → 422 (FastAPI); bad structure → 400.
- **Check:** 7 parametrised bad payloads → 400 and nothing persisted; real frontend shape accepted.
- **Result:** → **BUG-002**.

## M9 — `DELETE /api/state` ✅
- **What:** Deletes the row.
- **Check:** `test_reset`, `test_delete_when_empty_is_ok`.
- **Note:** Not called by the frontend — its "Reload demo / Empty fridge" buttons overwrite via PUT instead.

## M10 — Read-only views ✅
- **What:** `/api/items`, `/api/shopping`, `/api/stats` return slices of the state with
  `[]` / `{}` defaults.
- **Check:** empty defaults and post-save values verified.

## M11 — Transactions / rollback ✅
- **What:** `with conn:` inside `get_db()` commits on success, rolls back on exception.
- **Check:** `test_rollback_on_error` — insert followed by an exception leaves no row.

## M12 — Concurrency ✅ / ⚠️
- **What:** Sync endpoints run in FastAPI's threadpool, each with its own connection.
- **Check:** `test_concurrent_writes` — 30 simultaneous PUTs, zero errors.
- **Open:** Last write wins. Two open tabs/devices silently overwrite each other's changes
  (no version/ETag check on PUT).

## M13 — Error handling ✅
- **Check:** unknown route → 404; `POST /api/state` → 405; malformed JSON / non-object body → 422.

## M14 — Frontend load ⚠️
- **What:** `loadState()` GETs `/api/state`; uses it only if `version === STATE_VERSION` and
  `items` is an array, otherwise falls back to `makeSeed()` demo data.
- **Open:** If `STATE_VERSION` is ever bumped (or the server is briefly unreachable on load),
  the app shows demo data and the debounced auto-save then **overwrites the real saved state**
  with it. Needs a migration path / "don't save until load succeeded" guard.

## M15 — Frontend save 🔧 / ⚠️
- **What:** Every state change triggers `saveState()` after a 300 ms debounce.
- **Issue found:** `fetch()` only rejects on network failure, so a server rejection (400/422/500)
  was treated as success and the user was never told. → **BUG-003** (fixed).
- **Open:** A change made <300 ms before closing the tab is never sent.

## M16 — Security / limits ⚠️
- **Open:** No authentication; anyone who can reach the server can read or wipe the data.
  No request-size limit. Acceptable for local use; must be addressed before public hosting.
