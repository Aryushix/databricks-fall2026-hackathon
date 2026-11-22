import { useEffect, useRef, type ReactNode } from "react";
import { SERVICES, WITHIN } from "../labels";

export interface SearchValues {
  service: string;
  location: string;
  withinMinutes: number;
}

// Ask the bar to focus a field. `n` changes on every request so the
// same field can be focused again.
export interface FocusRequest {
  field: "service" | "location";
  n: number;
}

interface Props {
  values: SearchValues;
  onChange: (values: SearchValues) => void;
  onSearch: () => void;
  onReset: () => void;
  focus: FocusRequest | null;
  // The voice input control (MicButton).
  mic: ReactNode;
  // Optional button beside the Near input ("Use my location") and a
  // note shown under it.
  nearAction?: ReactNode;
  nearNote?: string | null;
  busy?: boolean;
}

export function SearchBar({
  values,
  onChange,
  onSearch,
  onReset,
  focus,
  mic,
  nearAction,
  nearNote,
  busy,
}: Props) {
  const serviceRef = useRef<HTMLSelectElement>(null);
  const locationRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!focus) return;
    (focus.field === "service" ? serviceRef : locationRef).current?.focus();
  }, [focus]);

  return (
    <form
      className="search"
      onSubmit={(e) => {
        e.preventDefault();
        onSearch();
      }}
    >
      <label className="field field-service">
        <span>Service</span>
        <select
          ref={serviceRef}
          required
          value={values.service}
          onChange={(e) => onChange({ ...values, service: e.target.value })}
        >
          {/* No preselected service: voice requests use the form's service
              only when the user actually chose one. */}
          <option value="" disabled>
            Choose a service
          </option>
          {SERVICES.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </select>
      </label>

      <div className="field field-near">
        <label htmlFor="near-input">Near</label>
        <div className="near-row">
          <input
            id="near-input"
            ref={locationRef}
            required
            type="text"
            value={values.location}
            placeholder="City, district or PIN code"
            aria-describedby={nearNote ? "near-note" : undefined}
            onChange={(e) => onChange({ ...values, location: e.target.value })}
          />
          {nearAction}
        </div>
        {nearNote && (
          <span id="near-note" className="near-note" role="status">
            {nearNote}
          </span>
        )}
      </div>

      <label className="field field-within">
        <span>Within</span>
        <select
          value={values.withinMinutes}
          onChange={(e) =>
            onChange({ ...values, withinMinutes: Number(e.target.value) })
          }
        >
          {WITHIN.map((w) => (
            <option key={w.value} value={w.value}>
              {w.label}
            </option>
          ))}
        </select>
      </label>

      {mic}

      <button type="submit" className="btn-primary" disabled={busy}>
        {busy ? "Searching…" : "Search"}
      </button>

      <button type="button" className="btn-text" onClick={onReset} disabled={busy}>
        Start over
      </button>
    </form>
  );
}
