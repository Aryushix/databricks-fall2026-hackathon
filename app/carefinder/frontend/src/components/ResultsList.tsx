import type { Hospital } from "../types";
import { TrustDots } from "./TrustDots";

interface Props {
  facilities: Hospital[];
  // Records the ranking set aside (e.g. conflicting). Never ranked.
  excluded: Hospital[];
  // True while the user is confirming a different search.
  stale: boolean;
  selectedId: string | null;
  onSelect: (id: string) => void;
}

export function formatDistance(h: Hospital): string {
  if (h.distance_km == null) return "distance unknown";
  const km = `${h.distance_km.toFixed(1)} km`;
  // Mock distances are invented; say so rather than imply road travel.
  return h.distance_type === "road" ? `${km} by road` : `${km} (approx.)`;
}

export function ResultsList({ facilities, excluded, stale, selectedId, onSelect }: Props) {
  return (
    <section
      className={stale ? "results stale" : "results"}
      aria-labelledby="results-heading"
    >
      <div className="section-head">
        <h2 id="results-heading" tabIndex={-1}>
          {stale ? "Previous search · " : ""}
          {facilities.length} {facilities.length === 1 ? "place" : "places"} · ranked
        </h2>
        <span className="muted small">Right level, nearby, strongest evidence</span>
      </div>

      <ol className="result-list">
        {facilities.map((h, i) => {
          const selected = h.facility_id === selectedId;
          return (
            <li key={h.facility_id}>
              <button
                type="button"
                className={selected ? "result-card selected" : "result-card"}
                aria-pressed={selected}
                onClick={() => onSelect(h.facility_id)}
              >
                <span className="rank" aria-hidden="true">
                  {i + 1}
                </span>
                <span className="result-body">
                  <span className="result-name">{h.name}</span>
                  <span className="result-sub">
                    {[h.facility_level, h.city, formatDistance(h)]
                      .filter(Boolean)
                      .join(" · ")}
                  </span>
                </span>
                <TrustDots label={h.trust_label} />
              </button>
            </li>
          );
        })}
      </ol>

      {excluded.length > 0 && (
        <div className="hidden-box">
          <strong>
            Hidden: {excluded.length} {excluded.length === 1 ? "place" : "places"} with
            conflicting records
          </strong>
          <ul>
            {excluded.map((h) => (
              <li key={h.facility_id}>
                {h.name}
                {h.evidence[0] ? ` — “${h.evidence[0].text}”` : ""}
              </li>
            ))}
          </ul>
          <span className="small">Not ranked. To be sent for checking on the Data quality view.</span>
        </div>
      )}

      <p className="legend muted small">
        Dots = how sure we are the place offers this service
      </p>
    </section>
  );
}
