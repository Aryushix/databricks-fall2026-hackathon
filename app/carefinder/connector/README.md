# Care Finder — teammate voice/chatbot connector

This add-on connects the Gemini voice interpreter from your teammate's ZIP to **your existing** `api.py` and `ApplicationOrchestrator`. It does not replace the six-file backend.

## Installation

1. Back up your current project.
2. Copy only these two files from `Databricks.zip` (`Databricks/orchestratorcodefiles/`) into your existing `C:\Databricks` folder: `speech.py` and `spoken.py`.
3. Copy `chatbot_connector.py` from this add-on into `C:\Databricks`.
4. Install dependencies:

   ```cmd
   python -m pip install google-genai python-multipart python-dotenv
   ```

5. At the top of your existing `api.py`, add:

   ```python
   from chatbot_connector import attach_chatbot
   ```

6. **Just after** `orchestrator = ApplicationOrchestrator(demo_mode=True)` in `api.py`, add:

   ```python
   attach_chatbot(app, orchestrator, demo_mode=True)
   ```

   Keep all your existing API code and `/api/process` endpoint as they are.

7. Set your Gemini API key **locally** in `C:\Databricks\.env` (do not commit or share the file):

   ```text
   GEMINI_API_KEY=your-own-key-here
   ```

8. Start your server as usual:

   ```cmd
   python -m uvicorn api:app --reload
   ```

## New route

`POST /api/voice` expects `multipart/form-data` with `audio` (16-kHz WAV preferred), `session_id`, and optional `language`, `form_service`, `form_location`, `confirmation_id`. Its result matches the ZIP's voice response shape: `{ "parsed": ..., "response": ... }`.

**Important:** when the user says Yes/No in response to a confirmation prompt, send the `confirmation_id` from the prompt they actually heard/saw. The connector deliberately rejects a spoken Yes/No without an ID. An old ID is rejected by your orchestrator. For nonconfirmation voice searches, the ID isn't needed.

The adapter uses `speech.to_user_request()` for ordinary searches and handles confirmation specially to bridge the ZIP's older API to your new confirmation-ID schema.

## Caveats

- A working Gemini key and model access are needed for live recognition; they cannot be verified with offline tests.
- Never copy the ZIP's `schemas.py`, `main.py`, `state_manager.py`, or `workflow_router.py` over your updated files. They are an older version without confirmation IDs.
- Do not run the ZIP's separate `app.py` alongside your own `api.py`; this would create a second application/state store and use `/api/request` rather than your existing `/api/process`.
- The teammate's bundled React frontend targets `/api/request`, while your backend uses `/api/process`. The UI also needs to pass confirmation IDs before it can work with your backend. Your frontend teammate can make those changes later.
- Avoid extracting or uploading the `.env`, `.venv`, `node_modules`, or `build` directories from the ZIP. Configure fresh credentials locally.
- The `spoken.py` templates currently support Hindi and English. Other languages need separate output support later.
