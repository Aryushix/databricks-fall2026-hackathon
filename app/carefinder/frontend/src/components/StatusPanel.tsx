import { useEffect, useRef } from "react";
import { serviceLabel } from "../labels";
import type { OrchestratorResponse } from "../types";

interface Props {
  response: OrchestratorResponse | null;
  networkError: string | null;
  retryable: boolean;
  busy: boolean;
  onConfirm: (yes: boolean) => void;
  onRetry: () => void;
  onAskForHelp: () => void;
  // Demo mode: searches the mock data covers, offered on no results.
  demoSearches: { service: string; location: string }[];
  onTryDemo: (search: { service: string; location: string }) => void;
}

// One prompt at a time, under the search strip. Shows what the
// orchestrator needs next (confirm, clarify) or what went wrong.
export function StatusPanel({
  response,
  networkError,
  retryable,
  busy,
  onConfirm,
  onRetry,
  onAskForHelp,
  demoSearches,
  onTryDemo,
}: Props) {
  const yesRef = useRef<HTMLButtonElement>(null);
  const status = response?.status;

  // Move focus to "Yes" so keyboard and screen-reader users land on
  // the question the app is asking.
  useEffect(() => {
    if (status === "confirmation_required") yesRef.current?.focus();
  }, [response, status]);

  if (networkError) {
    return (
      <div className="status status-error" role="alert" tabIndex={-1}>
        <p>{networkError}</p>
        {retryable && (
          <button type="button" className="btn-outline" onClick={onRetry} disabled={busy}>
            Try again
          </button>
        )}
      </div>
    );
  }

  if (!response) return null;

  switch (response.status) {
    case "confirmation_required": {
      const c = response.confirmation!;
      return (
        <div className="status status-confirm" role="alertdialog" aria-labelledby="confirm-q">
          <p id="confirm-q">
            <strong>{serviceLabel(c.service)}</strong> near <strong>{c.location}</strong>. Is
            that right?
          </p>
          <div className="status-actions">
            <button
              ref={yesRef}
              type="button"
              className="btn-primary"
              onClick={() => onConfirm(true)}
              disabled={busy}
            >
              Yes, search
            </button>
            <button
              type="button"
              className="btn-outline"
              onClick={() => onConfirm(false)}
              disabled={busy}
            >
              No, change
            </button>
          </div>
        </div>
      );
    }

    case "clarification_required":
    case "reset":
    case "assistance_requested":
      return (
        <div className="status" role="status" tabIndex={-1}>
          <p>{response.message}</p>
        </div>
      );

    case "no_results":
      return (
        <div className="status status-warn" role="status" tabIndex={-1}>
          {demoSearches.length > 0 ? (
            <>
              <p>
                <strong>The sample data has no places for this search.</strong> It only covers
                a few fictional searches in India. Try one:
              </p>
              <div className="status-actions">
                {demoSearches.map((d) => (
                  <button
                    key={`${d.service}|${d.location}`}
                    type="button"
                    className="btn-outline"
                    onClick={() => onTryDemo(d)}
                    disabled={busy}
                  >
                    {serviceLabel(d.service)} · {d.location}
                  </button>
                ))}
              </div>
            </>
          ) : (
            <p>
              <strong>No place with enough evidence was found for this search.</strong> Try a
              nearby city or district, or a wider travel time.
            </p>
          )}
          <p className="emergency-line">
            Emergency? <a href="tel:108">Call 108</a> first.
          </p>
          <div className="status-actions">
            <button type="button" className="btn-outline" onClick={onAskForHelp} disabled={busy}>
              Ask a person for help
            </button>
          </div>
        </div>
      );

    case "error":
      return (
        <div className="status status-error" role="alert" tabIndex={-1}>
          <p>{response.message}</p>
          <div className="status-actions">
            <button type="button" className="btn-outline" onClick={onRetry} disabled={busy}>
              Try again
            </button>
            <button type="button" className="btn-outline" onClick={onAskForHelp} disabled={busy}>
              Ask a person for help
            </button>
          </div>
        </div>
      );

    default:
      return null;
  }
}
