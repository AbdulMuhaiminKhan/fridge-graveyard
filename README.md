# The Fridge Graveyard

A food-waste detective app. React + Tailwind frontend, Python (FastAPI + SQLite) backend.

## Team

| Role                                | Name                  |
|-------------------------------------|-----------------------|
| Project Manager                     | Abdul Khan            |
| Product / Research Lead             | Jaclyn Grace Christy  |
| UX/UI Lead                          | Minha Ashehad Faizy   |
| Development + Quality / Media Lead  | Prathibha             |

## Project structure

```
frontend/index.html   the React app (single file)
backend/main.py       FastAPI server: serves the page and the /api endpoints
backend/fridge.db     SQLite database (created automatically on first run)
requirements.txt      Python dependencies
```

## Run it

Requires **Python 3.10 or newer** (FastAPI 0.162 and Uvicorn 0.54 no longer support 3.9).
Check with `python3 --version`. On macOS the built-in `python3` is 3.9, so use a newer one,
for example Homebrew's: `brew install python@3.12`, then create the venv with `python3.12`.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn backend.main:app --reload
```

Then open http://localhost:8000. API docs are at http://localhost:8000/docs.

## Features

- **Fridge:** track items by expiry, with a daily rescue mission and suggested actions.
- **Recipes:** 32 rescue recipes ranked by how much at-risk food they use, with estimated nutrition.
- **Food goals:** eating style, allergies, favourite and disliked ingredients, protein and fibre goals.
- **Premium (shown as a 7-day free trial, then €5 a month; demo price only, no real payments):**
  - Receipt scanner: snap a grocery receipt and add the food with estimated expiry dates. The photo is
    read on the device with Tesseract.js (free, open source) and never uploaded; the Python backend
    (`backend/receipts.py`) turns the text into items using a German/English food dictionary.
  - Weekly meal planner that plans 7 dinners around expiry dates and builds one shopping list.
  - Household syncing: share one fridge with flatmates using a 6-character household code.
  - Who-ate-what: every eaten or wasted item records who closed it, with a household scoreboard.
- **Shopping list** and **stats** (rescue rate, money saved).

## Accounts and households

Everyone logs in with a username and password. Passwords are stored as salted PBKDF2 hashes
(`backend/auth.py`), never as plain text. Every account belongs to a household, and the
household owns the fridge, shopping list, goals and stats.

- **Create account** without a code: you get your own new fridge.
- **Create account with a household code** (or join later on the Premium tab): you share that fridge.
- Changes sync to everyone in the household every few seconds, with a notice like
  "Minha updated the fridge". If two people save at the same moment, the later save is
  rejected (HTTP 409) and that screen reloads the latest version instead of overwriting it.
- **Leave household** gives you a fresh fridge of your own; the others keep the shared one.

When upgrading from the old single-fridge version, the first account created keeps the
existing saved fridge.

### Two-user demo on one computer

1. Start the app: `uvicorn backend.main:app --reload --port 8001` and open http://localhost:8001.
2. **Window 1 (normal Chrome window):** Create account, name "Abdul", username `abdul`.
   Open the **Premium** tab and copy the household code.
3. **Window 2 (Chrome incognito window, Cmd+Shift+N):** Create account, name "Minha",
   username `minha`, and paste the code into "Household code".
   Within a few seconds window 1 shows "Minha joined your household" and both members.
4. In window 2, mark an item as eaten. Window 1 shows "Minha updated the fridge" and the item disappears.
5. On the Premium tab, "Who ate what" shows who rescued what.

Tip: use a normal and an incognito window (or two different browsers), because two tabs of the
same window share the same login if "Keep me logged in" is ticked.

## API

All data endpoints need a login: `Authorization: Bearer <token>` from `/api/auth/login` or `/api/auth/register`.

| Method | Path                       | What it does                                                 |
|--------|----------------------------|--------------------------------------------------------------|
| POST   | `/api/auth/register`       | Create an account (optionally joining a household by code)   |
| POST   | `/api/auth/login`          | Log in, returns a session token                              |
| POST   | `/api/auth/logout`         | End the session                                              |
| GET    | `/api/auth/me`             | Current user, household code and members                     |
| GET    | `/api/households/{code}`   | Preview a household before joining (members, item count)     |
| POST   | `/api/household/join`      | Join a household by code                                     |
| POST   | `/api/household/leave`     | Leave and start a fresh household                            |
| GET    | `/api/state`               | Load the household's state, version and who saved it last    |
| PUT    | `/api/state`               | Save the state (`X-Base-Version` guards against overwrites)  |
| DELETE | `/api/state`               | Wipe this household's state (app reloads demo data)          |
| GET    | `/api/items`               | List fridge items                                            |
| GET    | `/api/shopping`            | List the shopping list                                       |
| GET    | `/api/stats`               | Rescued / wasted stats                                       |
| GET    | `/api/stats/members`       | Who-ate-what scoreboard for every household member           |
| POST   | `/api/receipts/parse`      | Turn receipt text into food items                            |

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

The tests use a temporary database, so your own `fridge.db` is never touched.
