import { FACTORS } from "../labels";
import type { Hospital } from "../types";
import { PhoneIcon } from "./icons";
import { formatDistance } from "./ResultsList";
import { TrustDots } from "./TrustDots";

interface Props {
  facility: Hospital;
  rank: number;
  // The facility ranked just below, for the "why #1 beat #2" line.
  next: Hospital | null;
  uncertainties: string[];
}

const SOURCE_FIELD_LABELS: Record<string, string> = {
  mock_description: "Facility description (fictional)",
};

export function WhyPanel({ facility, rank, next, uncertainties }: Props) {
  const factors = Object.entries(facility.score_breakdown);

  return (
    <aside className="why" aria-labelledby="why-name">
      <p className="eyebrow">
        #{rank} · {rank === 1 ? "Recommended" : "Backup"}
      </p>
      <h2 id="why-name">{facility.name}</h2>
      <p className="muted">
        {[facility.facility_level, formatDistance(facility), facility.cost_information]
          .filter(Boolean)
          .join(" · ")}
      </p>
      <TrustDots label={facility.trust_label} />

      <div className="why-actions">
        {facility.phone_number ? (
          <a className="btn-primary btn-grow" href={`tel:${facility.phone_number.replace(/\s/g, "")}`}>
            <PhoneIcon /> Call
          </a>
        ) : (
          <span className="btn-primary btn-grow is-disabled" aria-disabled="true">
            No phone listed
          </span>
        )}
        <button
          type="button"
          className="btn-outline btn-grow"
          disabled
          title="Coming soon"
        >
          Send to phone
        </button>
      </div>

      <p className="emergency-line">
        Emergency? <a href="tel:108">Call 108</a> first.
      </p>

      <h3>Why this place</h3>
      {facility.evidence.length ? (
        facility.evidence.map((ev, i) => (
          <figure key={i} className="quote">
            <blockquote>“{ev.text}”</blockquote>
            <figcaption className="small">
              {ev.source_url ? (
                <a href={ev.source_url} target="_blank" rel="noreferrer">
                  {SOURCE_FIELD_LABELS[ev.source_field] ?? ev.source_field}
                </a>
              ) : (
                (SOURCE_FIELD_LABELS[ev.source_field] ?? ev.source_field)
              )}
            </figcaption>
          </figure>
        ))
      ) : (
        <p className="muted">No supporting text found in the record.</p>
      )}

      {factors.length > 0 && (
        <>
          <h3>How we decided</h3>
          <dl className="factors">
            {factors.map(([key, value]) => {
              const f = FACTORS[key] ?? { label: key, max: 100 };
              const pct = Math.max(0, Math.min(100, (value / f.max) * 100));
              return (
                <div key={key} className="factor">
                  <dt>{f.label}</dt>
                  <dd>
                    <span className="bar" aria-hidden="true">
                      <span style={{ width: `${pct}%` }} />
                    </span>
                    <span className="factor-value">
                      {value}/{f.max}
                    </span>
                  </dd>
                </div>
              );
            })}
          </dl>
          {next && next.score != null && facility.score != null && (
            <p className="small muted">
              {rank === 1
                ? `Ranked above #2 (${next.name}): score ${facility.score} vs ${next.score}.`
                : `Next option: ${next.name} (score ${next.score}).`}
            </p>
          )}
        </>
      )}

      <div className="not-sure" role="note">
        <strong>Not sure:</strong>{" "}
        {uncertainties.length ? uncertainties.join(" ") : "Availability today."}{" "}
        Never confirmed by a health worker. <strong>Call ahead.</strong>
      </div>
    </aside>
  );
}
