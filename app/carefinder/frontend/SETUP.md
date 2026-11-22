# Care Finder — connected React frontend

This is your teammate's existing React/Vite dashboard, adapted to YOUR
working `api.py` and `connector/` voice integration. Your orchestrator code
and data services stay unchanged.

## 1. Copy the files

Copy the `frontend/` folder from this ZIP into `C:\Databricks\frontend\`.
Copy `frontend_config.py` into `C:\Databricks\frontend_config.py`.
Do not copy the teammate's old `app.py`, `schemas.py`, `main.py`, etc.
Do not copy any `.env` or `node_modules` from the teammate's ZIP.

## 2. Register the frontend configuration route in YOUR `api.py`

Add this near its imports:

```python
from frontend_config import attach_frontend_config
```

After your existing `app = FastAPI(...)` has been created, register:

```python
attach_frontend_config(app, demo_mode=True)
```

Keep your existing `attach_chatbot(app, orchestrator, demo_mode=True)` call.
Do not create a second FastAPI app or a second orchestrator.

Existing endpoints: `/health`, `/api/process`, `/api/voice`.
Added endpoint: `/api/config`.

## 3. Run TWO terminals (Windows Command Prompt)

Terminal 1 — existing backend:

```cmd
cd C:\Databricks
python -m uvicorn api:app --reload --port 8000
```

Terminal 2 — React frontend:

```cmd
cd C:\Databricks\frontend
npm install
npm run dev
```

Open **http://localhost:5173** (not the FastAPI `/docs` page).
The Vite proxy forwards browser `/api/*` calls to localhost:8000.
No CORS configuration or `app.py` is required for local development.

## 4. Browser verification

1. Select Dialysis / Gaya, click Search → confirmation dialog.
2. Click Yes → ranked *fictional* demo hospitals (if your mock data has Gaya).
3. Start over and speak in Hindi using the microphone → confirmation dialog.
4. Answer 'Haan' using the microphone → the response arrives from `/api/voice`.
5. Test No/change and inspect the message in the status bar.

Open browser DevTools → Network to verify typed requests hit `/api/process`
and recordings hit `/api/voice`. A microphone requires browser permission;
localhost works as a secure development origin.

## 5. Maps and optional configuration

The dashboard renders a preview map without a Google Maps key. For the
real map, set `MAPS_BROWSER_KEY` in your local root `.env` and ensure that
the key is restricted in Google Cloud to allowed HTTP referrers (localhost
for development) and appropriate Maps APIs. `MAPS_MAP_ID` is optional.
Never send `GEMINI_API_KEY` to the browser.

## Important notes

- The mock data is fictional. Delhi may show `no_results`; Gaya is a known
  demo dataset location in the teammate's ZIP.
- Typed confirmations and voice confirmations send `confirmation_id`.
  The microphone captures the ID when recording begins.
- The frontend UI speaks voice replies through browser `speechSynthesis`.
  The current `/api/process` endpoint may not return a `spoken` object for
  typed searches, so text search is displayed without automatic speech.
- All session state lives in the running backend process and is lost when
  it restarts. Start a new search if you restart the backend mid-flow.
- Do not expose your `.env`, Python `.venv`, or `node_modules` in Git.
