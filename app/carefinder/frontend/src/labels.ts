import type { TrustLabel } from "./types";

// Service values must match the keys the backend search understands
// (mock_services.MOCK_HOSPITALS uses lowercase service names).
export const SERVICES = [
  { value: "dialysis", label: "Dialysis" },
  { value: "emergency c-section", label: "Emergency C-section" },
  { value: "sick newborn care", label: "Sick newborn care" },
  { value: "trauma", label: "Trauma" },
  { value: "burn care", label: "Burn care" },
  { value: "mri", label: "MRI" },
] as const;

export const WITHIN = [
  { value: 60, label: "1 hour" },
  { value: 120, label: "2 hours by road" },
  { value: 240, label: "4 hours" },
] as const;

export function serviceLabel(value: string): string {
  return SERVICES.find((s) => s.value === value)?.label ?? value;
}

// Trust is always shown as dots + words, never colour alone.
export const TRUST: Record<TrustLabel, { dots: number; word: string }> = {
  confirmed: { dots: 3, word: "Confirmed" },
  likely: { dots: 2, word: "Likely" },
  claimed_only: { dots: 1, word: "Claimed only" },
  conflicting_info: { dots: 0, word: "Conflicting" },
};

// "How we decided" factors. The max values match the scale of the
// fictional MOCK_SCORE_BREAKDOWNS; the real ranking engine should
// send normalised factors instead.
export const FACTORS: Record<string, { label: string; max: number }> = {
  service_match: { label: "Text backs the claim", max: 40 },
  accessibility: { label: "Easy to reach", max: 25 },
  reliability: { label: "Record looks reliable", max: 20 },
  readiness: { label: "Ready for this service", max: 15 },
};
