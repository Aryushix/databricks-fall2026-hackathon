"""
speech.py

Care Finder — Speech Layer (Gemini)

Turns a recorded voice request into the
structured fields of a UserRequest.

Gemini acts only as the intent module:
it transcribes and extracts. It never
searches, ranks or recommends facilities.

Configuration:
    GEMINI_API_KEY         server-side key
    GEMINI_MODEL           default gemini-3.5-flash-lite
    GEMINI_FALLBACK_MODEL  default gemini-3.1-flash-lite; "" disables
    GEMINI_THINKING_LEVEL  MINIMAL (default) / LOW
"""

import logging
import os
import time
from typing import Literal, Optional

from pydantic import BaseModel, Field
from schemas import (
    Intent,
    SearchConfirmation,
    UserRequest,
)

# ==========================================
# 1. SERVICES THE APP CAN SEARCH
# ==========================================

# Must match frontend/src/labels.ts SERVICES
# and the service keys used by the search
# service (lowercase).
SERVICES = (
    "dialysis",
    "emergency c-section",
    "sick newborn care",
    "trauma",
    "burn care",
    "mri",
)

ServiceName = Literal[
    "dialysis",
    "emergency c-section",
    "sick newborn care",
    "trauma",
    "burn care",
    "mri",
    "other",
]

IntentName = Literal[
    "find_hospital",
    "refine_search",
    "confirm_search",
    "request_assistance",
    "reset_search",
    "unknown",
]


# ==========================================
# 2. STRUCTURED OUTPUT FROM GEMINI
# ==========================================

class ParsedSpeech(BaseModel):
    """
    What Gemini extracts from one recording.

    Also returned to the browser so the user
    can see exactly what was understood.
    """

    transcript: str = Field(
        description=(
            "Exactly what the speaker said, in the "
            "language and script they used."
        )
    )

    language: str = Field(
        description=(
            "BCP-47 code of the spoken language, "
            "e.g. 'hi', 'en', 'bn'."
        )
    )

    intent: IntentName

    service: Optional[ServiceName] = Field(
        default=None,
        description=(
            "Healthcare service requested, mapped "
            "to the closest listed value. 'other' "
            "if a service was named but none fit. "
            "null if no service was mentioned."
        )
    )

    service_text: Optional[str] = Field(
        default=None,
        description=(
            "The service in the speaker's own "
            "words, in English."
        )
    )

    location: Optional[str] = Field(
        default=None,
        description=(
            "City, district, village or PIN code "
            "exactly as named, written in English "
            "letters (e.g. 'Gaya', '823001'). "
            "null if none was said."
        )
    )

    service_ambiguous: bool = Field(
        default=False,
        description=(
            "True if the speaker mentioned a "
            "service but it is unclear which one."
        )
    )

    location_ambiguous: bool = Field(
        default=False,
        description=(
            "True if the place is unclear or "
            "could be several places."
        )
    )

    confirmation: Optional[bool] = Field(
        default=None,
        description=(
            "Only for intent confirm_search: true "
            "for yes, false for no."
        )
    )

    is_emergency: bool = Field(
        description=(
            "True if the speaker describes a life-"
            "threatening situation happening now "
            "(heavy bleeding, unconscious, not "
            "breathing, severe injury, labour "
            "complications, seizure, poisoning)."
        )
    )


SYSTEM_INSTRUCTION = """\
You are the speech front end of Care Finder, which helps people in India \
find a health facility that offers a service near a place.

Listen to one short recording and fill in the JSON fields. Rules:
- Only extract what the speaker actually said. Never guess a place or a \
service that was not mentioned; use null instead.
- Speakers may use Hindi, English, other Indian languages or a mix. \
Transcribe in the speaker's own script.
- Write the location in English letters as the speaker named it. Do not \
correct it to a different place.
- Map the service to the closest listed value: kidney failure or \
haemodialysis -> dialysis; caesarean, C-section or obstructed labour -> \
emergency c-section; newborn not breathing well, very small or sick baby -> \
sick newborn care; accident or serious injury -> trauma; burns -> burn care; \
MRI scan -> mri. Anything else -> other.
- intent:
  - find_hospital: asks for a service and/or place.
  - refine_search: changes only the service or the place of an earlier \
search ("not Gaya, Patna").
  - confirm_search: answers yes or no to the app's question.
  - request_assistance: asks to talk to a person.
  - reset_search: wants to start over.
  - unknown: anything else.
- You never recommend, rank or describe facilities.
"""


# ==========================================
# 3. CALL GEMINI
# ==========================================

# Cheapest tier; ~470 tokens and ~1 s per
# request on test clips. (gemini-2.5-flash
# is closed to new API users.)
DEFAULT_MODEL = "gemini-3.5-flash-lite"

# Tried when the main model is overloaded
# (Gemini 503 "high demand" happens).
DEFAULT_FALLBACK_MODEL = "gemini-3.1-flash-lite"

# Pause before retrying a temporary error.
RETRY_DELAY_SECONDS = 1.0

logger = logging.getLogger("care_finder.speech")

# The JSON reply is small; this caps cost if
# the model ever rambles.
MAX_OUTPUT_TOKENS = 256


def model_name() -> str:
    return os.getenv("GEMINI_MODEL") or DEFAULT_MODEL


def fallback_model_name() -> Optional[str]:
    value = os.getenv("GEMINI_FALLBACK_MODEL")
    if value is None:
        return DEFAULT_FALLBACK_MODEL
    return value.strip() or None


def _thinking_config(types):
    """
    Keep reasoning (and its tokens) to a
    minimum; extraction needs none.
    """

    return types.ThinkingConfig(
        thinking_level=os.getenv(
            "GEMINI_THINKING_LEVEL", "MINIMAL"
        ).upper()
    )


class SpeechNotConfigured(RuntimeError):
    pass


class SpeechRateLimited(RuntimeError):
    pass


class SpeechBusy(RuntimeError):
    """Gemini is temporarily overloaded (5xx)."""


class SpeechFailed(RuntimeError):
    pass


_client = None


def _get_client():
    """
    Create the Gemini client on first use.
    """

    global _client

    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        raise SpeechNotConfigured(
            "GEMINI_API_KEY is not set"
        )

    if _client is None:

        # Imported lazily so the rest of the
        # app runs without google-genai.
        from google import genai

        _client = genai.Client(api_key=api_key)

    return _client


def parse_audio(
    audio: bytes,
    mime_type: str,
    pending: Optional[SearchConfirmation] = None,
    ui_language: Optional[str] = None,
) -> ParsedSpeech:
    """
    Transcribe and interpret one recording.

    `pending` tells Gemini the app has just
    asked the user to confirm a search, so a
    bare "yes" / "हाँ" maps to confirm_search.
    """

    from google.genai import errors, types

    context = []

    if pending is not None:
        context.append(
            f"The app has just asked: "
            f"'{pending.service} near "
            f"{pending.location} — is that "
            f"right?' A yes/no answer is "
            f"confirm_search."
        )
    else:
        context.append(
            "No question is pending."
        )

    if ui_language:
        context.append(
            f"The app is set to language "
            f"'{ui_language}'."
        )

    # Main model twice (one retry after a
    # pause), then the fallback model once.
    attempts = [model_name(), model_name()]

    fallback = fallback_model_name()

    if fallback and fallback != model_name():
        attempts.append(fallback)

    response = None
    last_error = None

    for i, model in enumerate(attempts):

        try:
            response = _call_gemini(
                audio, mime_type, context, types, model
            )
            break

        except errors.APIError as error:

            last_error = error

            if error.code == 429:
                raise SpeechRateLimited(
                    "Gemini quota exceeded"
                ) from error

            # Only temporary server errors are
            # worth another try.
            if not (error.code and error.code >= 500):
                raise SpeechFailed(
                    f"Gemini error {error.code}"
                ) from error

            logger.warning(
                "Gemini %s on %s (attempt %d)",
                error.code, model, i + 1
            )

            if i == 0:
                time.sleep(RETRY_DELAY_SECONDS)

    if response is None:
        raise SpeechBusy(
            f"Gemini unavailable: {last_error}"
        ) from last_error

    parsed = response.parsed

    if not isinstance(parsed, ParsedSpeech):

        if not response.text:
            raise SpeechFailed(
                "Gemini returned no result"
            )

        parsed = ParsedSpeech.model_validate_json(
            response.text
        )

    return parsed


def _call_gemini(audio, mime_type, context, types, model):

    return _get_client().models.generate_content(
        model=model,
        contents=[
            types.Part.from_bytes(
                data=audio,
                mime_type=mime_type
            ),
            " ".join(context),
        ],
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=ParsedSpeech,
            temperature=0,
            max_output_tokens=MAX_OUTPUT_TOKENS,

            # Extraction needs no reasoning.
            thinking_config=_thinking_config(types),
        ),
    )


# ==========================================
# 4. PARSED SPEECH -> USER REQUEST
# ==========================================

def to_user_request(
    parsed: ParsedSpeech,
    session_id: str,
    form_service: Optional[str] = None,
    form_location: Optional[str] = None,
) -> UserRequest:
    """
    Build the UserRequest the orchestrator
    expects from what Gemini extracted.

    For a search, anything the speaker left
    out is taken from what is already in the
    search form ("nearest hospital?" with
    "Gaya" typed in Near). Spoken values win.
    The orchestrator still asks the user to
    confirm before searching.
    """

    intent = Intent(parsed.intent)

    confirmation = parsed.confirmation

    # A yes/no without a clear answer cannot
    # be a confirmation.
    if intent == Intent.CONFIRM_SEARCH and confirmation is None:
        intent = Intent.UNKNOWN

    # Keep the user's own words for services
    # outside the list; the search reports no
    # results rather than guessing.
    service = parsed.service

    if service == "other":
        service = (
            parsed.service_text.strip().lower()
            if parsed.service_text
            else None
        )

    location = parsed.location

    if intent in (
        Intent.FIND_HOSPITAL,
        Intent.REFINE_SEARCH,
    ):

        if (
            not service
            and not parsed.service_ambiguous
            and form_service
        ):
            service = form_service

        if (
            not location
            and not parsed.location_ambiguous
            and form_location
        ):
            location = form_location

    return UserRequest(
        session_id=session_id,
        intent=intent,
        service=service,
        location=location,
        service_ambiguous=parsed.service_ambiguous,
        location_ambiguous=parsed.location_ambiguous,
        confirmation=(
            confirmation
            if intent == Intent.CONFIRM_SEARCH
            else None
        ),
        raw_text=parsed.transcript,
        language=parsed.language,
        channel="web_voice",
    )
