"""The one request/response model, shared by the library (service.recommend) and the API.

The response never echoes the patient's message or coordinates. Coordinates in a recommendation
are the facility's.
"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

Urgency = Literal["emergency", "urgent", "routine", "unclear"]
Severity = Literal["none", "mild", "moderate", "severe", "unknown"]
Onset = Literal["sudden", "gradual", "unknown"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RecommendRequest(_Model):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    patient_message: str = Field(min_length=1, max_length=2000)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    max_results: int = Field(default=5, ge=1, le=20)

    @field_validator("patient_message")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("patient_message is blank")
        return v


class ContactNumber(_Model):
    label: str
    number: str
    alt_number: Optional[str] = None
    note: Optional[str] = None
    source_url: str


class EmergencyBlock(_Model):
    call_emergency_services: bool
    crisis: bool = False
    message: str
    category_messages: list[str] = []
    numbers: list[ContactNumber]
    transport_advice: Optional[str] = None


class Triage(_Model):
    urgency: Urgency
    rule_floor: Optional[Literal["urgent", "emergency"]] = None
    red_flag_rule_ids: list[str] = []
    red_flag_categories: list[str] = []
    raised_by_extraction: bool = False
    reason: str
    advice: str


class SymptomAnalysis(_Model):
    language: str = "unknown"
    body_locations: list[str] = []
    pain_type: Optional[str] = None
    severity: Severity = "unknown"
    onset: Onset = "unknown"
    duration: Optional[str] = None
    associated_symptoms: list[str] = []
    special_context: list[str] = []
    danger_signs: list[str] = []
    extraction_method: Literal["llm", "rules_fallback"] = "rules_fallback"
    extraction_confidence: Optional[float] = None
    missing_information: list[str] = []


class CareNeed(_Model):
    id: str
    role: Literal["required", "supporting"]
    verification_kb: Optional[str] = None
    proxy_kbs: list[str] = []


class EvidenceQuote(_Model):
    quote: str
    field: str
    source_type: str
    source_url: Optional[str] = None
    page_unknown: bool = False
    record_source_urls: list[str] = []


class MatchedNeed(_Model):
    care_need: str
    role: Literal["required", "supporting"]
    kb_id: str
    via: Literal["kb", "proxy_kb"] = "kb"
    status: str
    confidence: Optional[float] = None
    confidence_basis: Optional[str] = None
    reason_codes: list[str] = []
    kb_review_status: Optional[str] = None
    evidence: list[EvidenceQuote] = []


class UnverifiedSignal(_Model):
    care_need: str
    source: Literal["specialties", "facilityTypeId"]
    value: str


class TravelEstimate(_Model):
    road_distance_km: float
    duration_minutes: float
    mode: str
    provider: str


class Recommendation(_Model):
    rank: int
    # "nearest": emergencies only, the closest hospitals first (with any verified match they have);
    # "verified": capability matches by status tier, then confidence band, then distance;
    # "unverified_fallback": nearby facilities, capability not verified: call ahead
    list: Literal["nearest", "verified", "unverified_fallback"]
    hospital_id: str
    hospital_name: Optional[str] = None
    facility_type: Optional[str] = None
    latitude: float
    longitude: float
    straight_line_distance_km: float
    travel: Optional[TravelEstimate] = None
    location_status: Optional[str] = None
    listed_phone: Optional[str] = None          # from the facility listing; never checked by us
    listed_phone_note: Optional[str] = None
    capability_match: bool
    matched_care_needs: list[MatchedNeed] = []
    unverified_signals: list[UnverifiedSignal] = []
    unmatched_required_needs: list[str] = []
    warnings: list[str] = []
    requires_manual_review: bool = False
    review_reasons: list[str] = []
    also_listed_as: list[str] = []
    explanation: str


class SearchInfo(_Model):
    radius_km: float
    widened: bool = False
    nearest_facility_km: Optional[float] = None


class Versions(_Model):
    navigation: Optional[str] = None
    red_flag_rules: Optional[str] = None
    care_needs: Optional[str] = None
    kb: Optional[str] = None
    verification_run: Optional[str] = None


class RecommendResponse(_Model):
    request_id: str
    disclaimer: str
    emergency: Optional[EmergencyBlock] = None
    triage: Triage
    symptom_analysis: Optional[SymptomAnalysis] = None
    care_needs: list[CareNeed] = []
    recommendations: list[Recommendation] = []
    follow_up_questions: list[str] = []
    coverage_note: Optional[str] = None
    search: Optional[SearchInfo] = None
    degraded: bool = False
    degraded_reasons: list[str] = []
    versions: Versions = Versions()
