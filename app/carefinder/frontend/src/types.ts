// Mirrors the existing FastAPI /api/process and /api/voice responses.
// Keep these interfaces in sync with the backend schemas.

export type Intent =
  | "find_hospital"
  | "refine_search"
  | "confirm_search"
  | "request_assistance"
  | "reset_search"
  | "unknown";

export type ResponseStatus =
  | "results"
  | "clarification_required"
  | "confirmation_required"
  | "assistance_requested"
  | "no_results"
  | "error"
  | "reset";

export type EvidenceStatus = "supporting" | "contradictory" | "ambiguous";

export type TrustLabel =
  | "confirmed"
  | "likely"
  | "claimed_only"
  | "conflicting_info";

export interface Evidence {
  text: string;
  source_field: string;
  source_url: string | null;
  status: EvidenceStatus;
  source_date: string | null;
}

export interface Hospital {
  facility_id: string;
  name: string;
  city: string | null;
  state: string | null;
  latitude: number | null;
  longitude: number | null;
  distance_km: number | null;
  distance_type: string | null;
  phone_number: string | null;
  facility_level: string | null;
  cost_information: string | null;
  last_confirmed_date: string | null;
  score: number | null;
  score_breakdown: Record<string, number>;
  evidence: Evidence[];
  trust_label: TrustLabel | null;
  warnings: string[];
  explanation: string | null;
}

export interface SearchConfirmation {
  service: string;
  location: string;
  confirmation_id: string;
}

export interface SessionState {
  session_id: string;
  service: string | null;
  location: string | null;
  preferences: Record<string, unknown>;
  pending_confirmation: SearchConfirmation | null;
  confirmed_service: string | null;
  confirmed_location: string | null;
  last_facility_ids: string[];
  assistance_session_id: string | null;
}

export interface AnswerCard {
  primary_facility: Hospital;
  backup_facilities: Hospital[];
  uncertainties: string[];
  call_ahead: boolean;
}

export interface OrchestratorResponse {
  session_id: string;
  status: ResponseStatus;
  message: string;
  facilities: Hospital[];
  missing_fields: string[];
  warnings: string[];
  context: SessionState | null;
  assistance_session_id: string | null;
  confirmation: SearchConfirmation | null;
  answer_card: AnswerCard | null;
  excluded_facilities: Hospital[];
  // Added by app.py: the reply to read aloud, in both languages.
  spoken?: Spoken;
}

export interface Spoken {
  lang: "hi" | "en";
  hi: string;
  en: string;
}

// What the speech layer understood (speech.ParsedSpeech).
export interface ParsedSpeech {
  transcript: string;
  language: string;
  intent: Intent;
  service: string | null;
  service_text: string | null;
  location: string | null;
  service_ambiguous: boolean;
  location_ambiguous: boolean;
  confirmation: boolean | null;
  is_emergency: boolean;
}

// Response of POST /api/voice.
export interface VoiceResult {
  parsed: ParsedSpeech;
  response: OrchestratorResponse;
}

// Body for POST /api/process (api.py APIUserRequest).
export interface RequestBody {
  intent: Intent;
  service?: string | null;
  location?: string | null;
  preferences?: Record<string, unknown>;
  service_ambiguous?: boolean;
  location_ambiguous?: boolean;
  clear_service?: boolean;
  clear_location?: boolean;
  confirmation?: boolean | null;
  confirmation_id?: string | null;
  raw_text?: string | null;
  language?: string | null;
  channel?: string;
}
