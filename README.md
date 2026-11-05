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

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn backend.main:app --reload
```

Then open http://localhost:8000. API docs are at http://localhost:8000/docs.

## API

| Method | Path            | What it does                                  |
|--------|-----------------|-----------------------------------------------|
| GET    | `/api/state`    | Load the whole app state (null if none saved) |
| PUT    | `/api/state`    | Save the whole app state                      |
| DELETE | `/api/state`    | Wipe saved state (app reloads demo data)      |
| GET    | `/api/items`    | List fridge items                             |
| GET    | `/api/shopping` | List the shopping list                        |
| GET    | `/api/stats`    | Rescued / wasted stats                        |

The frontend loads state from `/api/state` on start and saves it back (debounced) after every change.
