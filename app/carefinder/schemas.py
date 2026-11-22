
"""
schemas.py

Care Finder — Application Orchestrator

Defines the data structures shared between:
- Language / intent understanding
- Application orchestrator
- Conversation state manager
- Workflow router
- RAG / recommendation engine
- Representative assistance
- Future frontend

No external packages required.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ==========================================
# 1. USER INTENT TYPES
# ==========================================

class Intent(str, Enum):

    FIND_HOSPITAL = "find_hospital"

    REFINE_SEARCH = "refine_search"

    # NEW: User responds to confirmation.
    CONFIRM_SEARCH = "confirm_search"

    REQUEST_ASSISTANCE = "request_assistance"

    RESET_SEARCH = "reset_search"

    UNKNOWN = "unknown"


# ==========================================
# 2. RESPONSE STATUS TYPES
# ==========================================

class ResponseStatus(str, Enum):

    RESULTS = "results"

    CLARIFICATION_REQUIRED = "clarification_required"

    # NEW: Service/location must be confirmed.
    CONFIRMATION_REQUIRED = "confirmation_required"

    ASSISTANCE_REQUESTED = "assistance_requested"

    NO_RESULTS = "no_results"

    ERROR = "error"

    RESET = "reset"


# ==========================================
# 3. SEARCH CONFIRMATION
# ==========================================

@dataclass
class SearchConfirmation:
    """
    Stores the search criteria awaiting
    explicit user confirmation.

    Example:
        service = "dialysis"
        location = "Gaya"

    The chatbot can present:
        "Dialysis near Gaya. Is that correct?"
    """

    service: str
    location: str


# ==========================================
# 4. INCOMING USER REQUEST
# ==========================================

@dataclass
class UserRequest:
    """
    Structured request received by the
    application orchestrator.

    Eventually produced by the intent module.

    For now, requests can be created manually
    for testing.

    The orchestrator does not need to know
    how the user originally expressed the
    request.
    """

    session_id: str

    intent: Intent

    service: Optional[str] = None

    location: Optional[str] = None

    preferences: dict[str, Any] = field(
        default_factory=dict
    )

    # Indicates uncertainty in interpretation.
    service_ambiguous: bool = False

    location_ambiguous: bool = False

    # Allows explicit changes to existing state.
    clear_service: bool = False

    clear_location: bool = False

    # NEW: Explicit confirmation response.
    #
    # True  = user accepted proposed search
    # False = user rejected proposed search
    # None  = user has not confirmed
    confirmation: Optional[bool] = None
    confirmation_id: Optional[str] = None

    # Optional original input.
    raw_text: Optional[str] = None

    # Channel and language metadata.
    # These do not change routing rules.
    language: Optional[str] = None

    channel: str = "web"

    def __post_init__(self):

        if not self.session_id or not self.session_id.strip():
            raise ValueError(
                "session_id cannot be empty"
            )

        if self.intent == Intent.CONFIRM_SEARCH:

            if self.confirmation is None:
                raise ValueError(
                    "CONFIRM_SEARCH requires "
                    "confirmation=True or False"
                )

            if (
                not self.confirmation_id
                or not self.confirmation_id.strip()
            ):
                raise ValueError(
                    "CONFIRM_SEARCH requires confirmation_id"
                )


# ==========================================
# 5. CONVERSATION SESSION STATE
# ==========================================

@dataclass
class SessionState:
    """
    Remembers conversation context.

    service/location:
        Currently interpreted search criteria.

    pending_confirmation:
        Proposed criteria awaiting user approval.

    confirmed_service/confirmed_location:
        Most recently user-confirmed search criteria.

    A search is allowed only when the current
    criteria match the confirmed criteria.
    """

    session_id: str

    service: Optional[str] = None

    location: Optional[str] = None

    preferences: dict[str, Any] = field(
        default_factory=dict
    )

    # NEW: Pending user confirmation.
    pending_confirmation: Optional[
        SearchConfirmation
    ] = None

    # NEW: Last confirmed criteria.
    confirmed_service: Optional[str] = None

    confirmed_location: Optional[str] = None

    # Previously recommended hospitals.
    last_facility_ids: list[str] = field(
        default_factory=list
    )

    # Representative-assistance session.
    assistance_session_id: Optional[str] = None


# ==========================================
# 6. STRUCTURED HOSPITAL SEARCH REQUEST
# ==========================================

@dataclass
class SearchConfirmation:
    """
    A specific search proposal awaiting
    confirmation from the user.
    """

    service: str
    location: str

    # Unique identifier for this proposal.
    confirmation_id: str


# ==========================================
# 7. FACILITY EVIDENCE TYPES
# ==========================================

class EvidenceStatus(str, Enum):

    SUPPORTING = "supporting"

    CONTRADICTORY = "contradictory"

    AMBIGUOUS = "ambiguous"


class TrustLabel(str, Enum):
    """
    Trust classification from the
    evidence evaluation system.

    CONFIRMED refers to evidence verification,
    NOT confirmation of live availability.
    """

    CONFIRMED = "confirmed"

    LIKELY = "likely"

    CLAIMED_ONLY = "claimed_only"

    CONFLICTING_INFO = "conflicting_info"


@dataclass
class Evidence:
    """
    Original evidence associated with a
    facility or service claim.

    Exact source text must be preserved.
    """

    text: str

    source_field: str

    source_url: Optional[str] = None

    status: EvidenceStatus = (
        EvidenceStatus.AMBIGUOUS
    )

    # Only populated when the date is known.
    source_date: Optional[str] = None


# ==========================================
# 8. HOSPITAL RESULT
# ==========================================

@dataclass
class Hospital:
    """
    Standard internal facility representation.

    The future data adapter converts
    Databricks records into this structure.

    No assumptions about dataset columns
    are made here.
    """

    facility_id: str

    name: str

    city: Optional[str] = None

    state: Optional[str] = None

    latitude: Optional[float] = None

    longitude: Optional[float] = None

    distance_km: Optional[float] = None

    # "straight_line" or "road", when known.
    distance_type: Optional[str] = None

    # NEW: Optional facility information.
    phone_number: Optional[str] = None

    facility_level: Optional[str] = None

    cost_information: Optional[str] = None

    last_confirmed_date: Optional[str] = None

    # Ranking system provides these.
    score: Optional[float] = None

    score_breakdown: dict[str, float] = field(
        default_factory=dict
    )

    # Evidence and reliability.
    evidence: list[Evidence] = field(
        default_factory=list
    )

    trust_label: Optional[TrustLabel] = None

    warnings: list[str] = field(
        default_factory=list
    )

    explanation: Optional[str] = None


# ==========================================
# 9. STANDARDIZED ANSWER CARD
# ==========================================

@dataclass
class AnswerCard:
    """
    Shared recommendation result.

    Different interfaces can present
    the same underlying recommendation:

    - Short spoken response
    - Mobile screen card
    - Detailed dashboard view

    Presentation and translation are
    handled by their respective modules.
    """

    primary_facility: Hospital

    backup_facilities: list[Hospital] = field(
        default_factory=list
    )

    uncertainties: list[str] = field(
        default_factory=list
    )

    # Encourages users to verify availability.
    call_ahead: bool = True


# ==========================================
# 10. ORCHESTRATOR RESPONSE
# ==========================================

@dataclass
class OrchestratorResponse:
    """
    Standard response returned by
    the application orchestrator.

    Supports:
    - Search results
    - Clarification
    - Confirmation
    - Representative assistance
    - No results
    - Errors
    - Search reset
    """

    session_id: str

    status: ResponseStatus

    message: str

    facilities: list[Hospital] = field(
        default_factory=list
    )

    missing_fields: list[str] = field(
        default_factory=list
    )

    warnings: list[str] = field(
        default_factory=list
    )

    context: Optional[SessionState] = None

    assistance_session_id: Optional[str] = None

    # NEW: Details of a confirmation request.
    confirmation: Optional[
        SearchConfirmation
    ] = None

    # NEW: Shared evidence-based recommendation.
    answer_card: Optional[AnswerCard] = None

# ==========================================
# STRUCTURED HOSPITAL SEARCH QUERY
# ==========================================

@dataclass
class SearchQuery:
    """
    Represents a confirmed search request
    sent to the hospital retrieval service.
    """

    service: str

    location: str

    preferences: dict[str, Any] = field(
        default_factory=dict
    )
