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
- **Extras (all free):**
  - Weekly meal planner that plans 7 dinners around expiry dates and builds one shopping list.
  - Household syncing: share one fridge with flatmates using a 6-character household code.
  - Who-ate-what: every eaten or wasted item records who closed it, with a household scoreboard.
- **Shopping list** and **stats** (rescue rate, money saved).

## Households

Every browser gets its own household on first visit, so visitors never see each other's fridge.
The code is shown on the Extras tab. Entering a flatmate's code joins their fridge, and changes
sync to the other phones within a few seconds. If two people save at the same moment, the later
save is rejected (HTTP 409) and that phone reloads the latest version instead of overwriting it.

When upgrading from the old single-fridge version, the first browser that opens the app keeps
the existing saved fridge.

## API

All state endpoints need the household code in an `X-Household` header.

| Method | Path                       | What it does                                              |
|--------|----------------------------|-----------------------------------------------------------|
| POST   | `/api/households`          | Create a household and return its code                    |
| GET    | `/api/households/{code}`   | Check a code before joining (item count, last update)     |
| GET    | `/api/state`               | Load the household's state and its version                |
| PUT    | `/api/state`               | Save the state (`X-Base-Version` guards against overwrites)|
| DELETE | `/api/state`               | Wipe this household's state (app reloads demo data)       |
| GET    | `/api/items`               | List fridge items                                         |
| GET    | `/api/shopping`            | List the shopping list                                    |
| GET    | `/api/stats`               | Rescued / wasted stats                                    |
| GET    | `/api/stats/members`       | Who-ate-what scoreboard per household member              |

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

The tests use a temporary database, so your own `fridge.db` is never touched.
