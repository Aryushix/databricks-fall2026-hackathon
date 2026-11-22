
"""
api.py

Care Finder — FastAPI Backend

HTTP interface for the existing ApplicationOrchestrator.

Endpoints:
    GET  /health
    POST /api/process

All hospital data comes from mock services for now.
No real medical facility information is served.
"""

from typing import Any, Optional

from connector.chatbot_connector import attach_chatbot
from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from frontend_config import attach_frontend_config
from main import ApplicationOrchestrator
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from schemas import Intent, UserRequest
from recommendation_connector import (
    RecommendationSearchService,
    PreserveRecommendationRanking,
)

# ==========================================
# 1. INITIALIZE FASTAPI
# ==========================================

app = FastAPI(
    title="Care Finder API",
    description=(
        "Backend API for the Care Finder "
        "application orchestrator. "
        "Currently uses fictional hospital data."
    ),
    version="0.1.0",
)


# ==========================================
# 2. CONFIGURE LOCAL FRONTEND ACCESS
# ==========================================

# Allow a future Next.js development frontend
# to communicate with this backend.
#
# These origins are for LOCAL development only.

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


# ==========================================
# 3. DEFINE API REQUEST MODEL
# ==========================================

class APIUserRequest(BaseModel):
    """
    Incoming JSON request.

    This mirrors schemas.UserRequest while
    allowing FastAPI to validate HTTP input.
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    session_id: str = Field(
        min_length=1
    )

    intent: Intent

    service: Optional[str] = None

    location: Optional[str] = None

    preferences: dict[str, Any] = Field(
        default_factory=dict
    )

    service_ambiguous: bool = False

    location_ambiguous: bool = False

    clear_service: bool = False

    clear_location: bool = False

    confirmation: Optional[bool] = None
    confirmation_id: Optional[str] = None

    raw_text: Optional[str] = None

    language: Optional[str] = None

    channel: str = "web"

    # Reject blank or whitespace-only IDs.
    @field_validator("session_id")
    @classmethod
    def validate_session_id(cls, value: str):
        value = value.strip()

        if not value:
            raise ValueError(
                "session_id cannot be empty"
            )

        return value

    # Validate confirmation requests.
    
    @model_validator(mode="after")
    def validate_confirmation(self):

        if self.intent == Intent.CONFIRM_SEARCH:

            # Require an explicit Yes or No.
            if self.confirmation is None:
                raise ValueError(
                    "CONFIRM_SEARCH requires "
                    "confirmation=True or False"
                )

            # Require the specific proposal ID.
            if (
                not self.confirmation_id
                or not self.confirmation_id.strip()
            ):
                raise ValueError(
                    "CONFIRM_SEARCH requires "
                    "a nonblank confirmation_id"
                )

            # Don't allow a confirmation request
            # to simultaneously edit the search.
            if (
                self.service is not None
                or self.location is not None
                or self.clear_service
                or self.clear_location
                or self.preferences
            ):
                raise ValueError(
                    "Confirmation requests cannot "
                    "also modify search criteria"
                )

        else:

            # Confirmation fields only belong
            # to confirmation requests.
            if (
                self.confirmation is not None
                or self.confirmation_id is not None
            ):
                raise ValueError(
                    "Confirmation fields are only "
                    "valid with CONFIRM_SEARCH"
                )

        return self



# ==========================================
# 4. INITIALIZE THE ORCHESTRATOR
# ==========================================

# Create ONE orchestrator instance for this
# local development server process.
#
# This preserves conversation state across
# HTTP requests handled by that process.
#
# Production will require persistent storage.

orchestrator = ApplicationOrchestrator(
    search_service=RecommendationSearchService(),
    ranking_service=PreserveRecommendationRanking(),
    demo_mode=False,
)

attach_chatbot(app, orchestrator, demo_mode=False)
attach_frontend_config(app, demo_mode=False)

# ==========================================
# 5. HEALTH CHECK ENDPOINT
# ==========================================

@app.get("/health")
def health_check():
    """
    Verify that the HTTP server is running.

    Does not verify Databricks or external
    service connectivity.
    """

    return {
        "status": "ok",
        "service": "care-finder-api",
        "mode": "demo",
    }


# ==========================================
# 6. MAIN ORCHESTRATOR ENDPOINT
# ==========================================

@app.post("/api/process")
def process_request(payload: APIUserRequest):
    """
    Process one structured user request.

    Steps:
    1. FastAPI validates incoming JSON.
    2. Convert JSON into our UserRequest.
    3. Call ApplicationOrchestrator.process().
    4. Convert the result to JSON.
    5. Return the response to the caller.
    """

    # Convert validated API input into our
    # existing orchestrator request structure.
    request = UserRequest(
        **payload.model_dump()
    )

    # Execute existing orchestrator logic.
    response = orchestrator.process(
        request
    )

    # Convert dataclasses and enum values
    # into JSON-compatible structures.
    return jsonable_encoder(response)
