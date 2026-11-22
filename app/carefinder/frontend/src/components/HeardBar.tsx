import { serviceLabel } from "../labels";
import type { ParsedSpeech } from "../types";

// Shows what the speech layer heard and understood, so the user can
// spot a mistake before confirming.
export function HeardBar({ heard }: { heard: ParsedSpeech }) {
  const service =
    heard.service === "other" ? heard.service_text : heard.service && serviceLabel(heard.service);

  return (
    <div className="heard" aria-label="What we heard">
      <p className="heard-transcript">
        <span className="heard-label">You said</span>
        <q lang={heard.language}>{heard.transcript}</q>
      </p>
      {(service || heard.location) && (
        <p className="heard-understood small">
          Understood:{" "}
          {service && <span className="chip">{service}</span>}{" "}
          {heard.location && <span className="chip">near {heard.location}</span>}
          {(heard.service_ambiguous || heard.location_ambiguous) && " (unclear — please check)"}
        </p>
      )}
    </div>
  );
}
