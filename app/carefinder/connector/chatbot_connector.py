"""Attach the teammate's Gemini voice interpreter to an existing Care Finder FastAPI app.

Requires the teammate's speech.py and spoken.py alongside api.py.
Does not replace the existing orchestrator or its confirmation-ID logic.
"""

import logging
from typing import Optional

from dotenv import load_dotenv
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.encoders import jsonable_encoder
from schemas import Intent, UserRequest

from . import speech, spoken

load_dotenv()
logger = logging.getLogger("care_finder.chatbot_connector")

MAX_AUDIO_BYTES = 4 * 1024 * 1024
AUDIO_TYPES = {
    "audio/wav", "audio/x-wav", "audio/wave", "audio/mpeg",
    "audio/mp3", "audio/ogg", "audio/flac", "audio/aac",
}


def attach_chatbot(app, orchestrator, demo_mode=True):
    """Add POST /api/voice to the existing FastAPI app.

    The frontend sends multipart form data:
      audio (required), session_id (required), language,
      form_service, form_location, and confirmation_id.

    A spoken Yes/No MUST include the ID of the prompt displayed to the user.
    This prevents stale replies from confirming a newer search.
    """

    router = APIRouter()

    @router.post("/api/voice")
    def voice_request(
        audio: UploadFile = File(...),
        session_id: str = Form(...),
        language: Optional[str] = Form(None),
        form_service: Optional[str] = Form(None),
        form_location: Optional[str] = Form(None),
        confirmation_id: Optional[str] = Form(None),
    ):
        session_id = session_id.strip()
        if not session_id:
            raise HTTPException(422, "session_id cannot be empty")

        data = audio.file.read(MAX_AUDIO_BYTES + 1)
        if not data:
            raise HTTPException(400, "The recording is empty")
        if len(data) > MAX_AUDIO_BYTES:
            raise HTTPException(413, "Recording exceeds 4 MB")

        mime_type = (audio.content_type or "").split(";")[0].strip().lower()
        if mime_type not in AUDIO_TYPES:
            raise HTTPException(415, "Unsupported audio type")

        state = orchestrator.state_manager.get(session_id)
        pending = state.pending_confirmation if state is not None else None

        try:
            parsed = speech.parse_audio(
                data, mime_type, pending=pending, ui_language=language
            )
        except speech.SpeechNotConfigured:
            raise HTTPException(503, "Voice input is not configured")
        except speech.SpeechRateLimited:
            raise HTTPException(429, "Voice quota exceeded; try typing")
        except speech.SpeechBusy:
            raise HTTPException(503, "Voice service is busy; try typing")
        except Exception:
            logger.exception("Voice interpretation failed")
            raise HTTPException(502, "Voice input could not be understood")

        try:
            if parsed.intent == Intent.CONFIRM_SEARCH.value and parsed.confirmation is not None:
                # The teammate's to_user_request() has no confirmation_id support.
                # Construct this request ourselves, using the ID supplied by the UI.
                if not confirmation_id or not confirmation_id.strip():
                    raise HTTPException(
                        422,
                        "A spoken Yes/No must include the displayed confirmation_id",
                    )
                request = UserRequest(
                    session_id=session_id,
                    intent=Intent.CONFIRM_SEARCH,
                    confirmation=parsed.confirmation,
                    confirmation_id=confirmation_id.strip(),
                    raw_text=parsed.transcript,
                    language=parsed.language,
                    channel="web_voice",
                )
            else:
                request = speech.to_user_request(
                    parsed,
                    session_id,
                    form_service=(form_service or "").strip() or None,
                    form_location=(form_location or "").strip() or None,
                )
        except ValueError as exc:
            raise HTTPException(422, str(exc))

        response = orchestrator.process(request)
        response_json = jsonable_encoder(response)
        response_json["spoken"] = {
            "lang": spoken.pick_language(parsed.language, language),
            **{
                lang: spoken.spoken_text(
                    response, lang,
                    emergency=parsed.is_emergency,
                    demo=demo_mode,
                )
                for lang in spoken.SUPPORTED
            },
        }
        return {
            "parsed": parsed.model_dump(),
            "response": response_json,
        }

    app.include_router(router)
