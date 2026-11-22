import { useCallback, useRef, useState } from "react";
import type { OrchestratorResponse, RequestBody, VoiceResult } from "../types";

const SESSION_KEY = "care-finder-session";

// FastAPI errors may be strings or validation-error arrays.
function errorMessage(payload: unknown, status: number): string {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((item: { msg?: string; loc?: unknown[] }) => item.msg ?? "Invalid request")
        .join("; ");
    }
  }
  return `Server error (${status})`;
}

function getSessionId(): string {
  try {
    const existing = sessionStorage.getItem(SESSION_KEY);
    if (existing) return existing;
    const id = crypto.randomUUID();
    sessionStorage.setItem(SESSION_KEY, id);
    return id;
  } catch {
    // Storage blocked (private mode etc.): session lasts for this page.
    return crypto.randomUUID();
  }
}

/**
 * Talks to POST /api/process.
 *
 * `latest` is the most recent orchestrator response (drives prompts).
 * `results` is the last results / no_results response, kept while the
 * user confirms or clarifies a new search so the screen doesn't empty.
 */
export function useOrchestrator() {
  const sessionId = useRef(getSessionId()).current;
  const lastBody = useRef<RequestBody | null>(null);
  // Always use the most recent completed response, even across async callbacks.
  const latestRef = useRef<OrchestratorResponse | null>(null);

  const [latest, setLatest] = useState<OrchestratorResponse | null>(null);
  const [results, setResults] = useState<OrchestratorResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [networkError, setNetworkError] = useState<string | null>(null);
  // Typed requests can be resent; a failed recording must be re-spoken.
  const [retryable, setRetryable] = useState(false);

  // Apply a response from any endpoint (also used by voice in step 6).
  const accept = useCallback((response: OrchestratorResponse) => {
    latestRef.current = response;
    setLatest(response);
    if (response.status === "results" || response.status === "no_results") {
      setResults(response);
    } else if (response.status === "reset") {
      setResults(null);
    }
  }, []);

  const send = useCallback(
    async (body: RequestBody) => {
      lastBody.current = body;
      setRetryable(true);
      setBusy(true);
      setNetworkError(null);
      try {
        // A clicked confirmation must carry the ID of the displayed prompt.
        const confirmationId =
          body.intent === "confirm_search"
            ? (body.confirmation_id ??
              latestRef.current?.confirmation?.confirmation_id ??
              latestRef.current?.context?.pending_confirmation?.confirmation_id)
            : undefined;
        const res = await fetch("/api/process", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            channel: "web", ...body,
            ...(confirmationId ? { confirmation_id: confirmationId } : {}),
            session_id: sessionId,
          }),
        });
        if (!res.ok) {
          const detail = await res.json().catch(() => null);
          throw new Error(errorMessage(detail, res.status));
        }
        accept((await res.json()) as OrchestratorResponse);
      } catch (err) {
        setNetworkError(
          err instanceof TypeError
            ? "Could not reach the Care Finder server."
            : String((err as Error).message),
        );
      } finally {
        setBusy(false);
      }
    },
    [sessionId, accept],
  );

  // Send a recording to /api/voice. Returns what was understood and the
  // response, or null on failure (shown through networkError).
  const sendVoice = useCallback(
    async (
      audio: Blob,
      language: string,
      // Current form values, used for whatever the speaker leaves out.
      form: { service: string; location: string },
      confirmationId: string | null,
    ): Promise<VoiceResult | null> => {
      lastBody.current = null;
      setRetryable(false);
      setBusy(true);
      setNetworkError(null);
      try {
        const body = new FormData();
        body.append("audio", audio, "speech.wav");
        body.append("session_id", sessionId);
        body.append("language", language);
        body.append("form_service", form.service);
        body.append("form_location", form.location);
        if (confirmationId) body.append("confirmation_id", confirmationId);
        const res = await fetch("/api/voice", { method: "POST", body });
        if (!res.ok) {
          const detail = await res.json().catch(() => null);
          throw new Error(errorMessage(detail, res.status));
        }
        const data = (await res.json()) as VoiceResult;
        accept(data.response);
        return data;
      } catch (err) {
        setNetworkError(
          err instanceof TypeError
            ? "Could not reach the Care Finder server."
            : String((err as Error).message),
        );
        return null;
      } finally {
        setBusy(false);
      }
    },
    [sessionId, accept],
  );

  const retry = useCallback(() => {
    if (lastBody.current) void send(lastBody.current);
  }, [send]);

  return { sessionId, latest, results, busy, networkError, retryable, send, sendVoice, retry, accept };
}
