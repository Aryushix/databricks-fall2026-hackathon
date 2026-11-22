import { TRUST } from "../labels";
import type { TrustLabel } from "../types";

export function TrustDots({ label }: { label: TrustLabel | null }) {
  const trust = label ? TRUST[label] : null;
  const filled = trust?.dots ?? 0;
  const word = trust?.word ?? "Unknown";

  return (
    <span className="trust" aria-label={`${word}, ${filled} of 3`}>
      <span className="trust-dots" aria-hidden="true">
        {[0, 1, 2].map((i) => (
          <span key={i} className={i < filled ? "dot dot-on" : "dot"} />
        ))}
      </span>
      <span className="trust-word">{word}</span>
    </span>
  );
}
