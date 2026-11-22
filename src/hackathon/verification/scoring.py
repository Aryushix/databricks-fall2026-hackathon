"""heuristic_v1: log-odds accumulation over independent origin clusters.

  logit = prior(specialization, facility type)
        + sum over origin clusters of  direct_llr   * (best positive claim's strength in that cluster)
        - sum over origin clusters of  negative_llr * (best negative claim's strength in that cluster)
        + capped prerequisite term

A cluster contributes its single strongest claim, never a sum, because claims inside one cluster share a
lineage. The weights are hand-set in configs/verification.yaml. The result is a score, not a calibrated
probability: replace this class with a fitted model (same interface) once labeled pairs exist.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

from .kb import KBEntry
from .records import Evidence

POSITIVE_TYPES = ("OFFERED", "PARTNER_ON_SITE", "AMBIGUOUS", "RESEARCH_OR_LISTING", "OUTREACH_CAMP", "PERSONNEL_ONLY")
NOT_PRESENT_TYPES = {"NOT_OFFERED", "DISCONTINUED", "REFERRAL_ONLY", "OUTSOURCED_OFF_SITE", "PLANNED_OR_COMING_SOON",
                     "EDUCATIONAL_OR_GENERIC", "RESEARCH_OR_LISTING", "EXTRACTOR_ARTIFACT"}


@dataclass
class ScoreResult:
    p_provides: float
    logit: float
    prior: float
    terms: list[dict] = field(default_factory=list)
    prerequisites_present: set[str] = field(default_factory=set)


class Scorer(Protocol):
    basis: str

    def score(self, items: list[Evidence], entry: KBEntry, facility_type: str | None) -> ScoreResult: ...


def is_stale(ev: Evidence, years: int, today: date | None = None) -> bool:
    if ev.temporal == "past":
        return True
    d = ev.source_date                      # retrieval dates are "as of fetching", so never stale here
    if not d:
        return False
    today = today or date.today()
    try:
        return int(d[:4]) < today.year - years
    except ValueError:
        return False


def is_negative(ev: Evidence, negative_types) -> bool:
    """A credible negative about the treatment itself (not a child, not a subgroup, not another facility)."""
    return (ev.role == "direct" and ev.assertion_type in negative_types and ev.negates_parent
            and ev.scope == "all" and ev.entity_match in ("this", "unnamed"))


class HeuristicScorer:
    basis = "heuristic_v1"

    def __init__(self, cfg: dict):
        self.cfg = cfg["scoring"]
        self.rel = cfg["reliability"]

    def reliability(self, ev: Evidence) -> float:
        return float(self.rel.get(ev.source_type, 0.0))

    def recency(self, ev: Evidence) -> float:
        r = self.cfg["recency"]
        return r["stale"] if ev.stale else (r["current"] if ev.date else r["undated"])

    def positive_strength(self, ev: Evidence) -> float:
        w = self.cfg["assertion_weight"].get(ev.assertion_type, 0.0)
        if ev.role not in ("direct", "ambiguous") or w == 0.0:
            return 0.0
        s = (w * self.reliability(ev) * ev.specificity * self.recency(ev)
             * self.cfg["entity"].get(ev.entity_match, 0.0))
        return s * (self.cfg["copied_factor"] if ev.copied else 1.0)

    def negative_strength(self, ev: Evidence) -> float:
        if not is_negative(ev, self.cfg["negative_types"]):
            return 0.0
        return self.reliability(ev) * self.recency(ev) * self.cfg["entity"].get(ev.entity_match, 0.0)

    def prior(self, entry: KBEntry, facility_type: str | None) -> float:
        table = self.cfg["prior_logit"].get(entry.specialization) or self.cfg["prior_logit"]["advanced"]
        return float(table.get(facility_type or "", table["default"]))

    def score(self, items: list[Evidence], entry: KBEntry, facility_type: str | None) -> ScoreResult:
        prior = self.prior(entry, facility_type)
        terms, best_pos, best_neg = [], {}, {}
        for ev in items:
            pos, neg = self.positive_strength(ev), self.negative_strength(ev)
            ev.weight = round(self.cfg["direct_llr"] * pos - self.cfg["negative_llr"] * neg, 4)
            if pos > best_pos.get(ev.origin_cluster_id, (0.0, None))[0]:
                best_pos[ev.origin_cluster_id] = (pos, ev)
            if neg > best_neg.get(ev.origin_cluster_id, (0.0, None))[0]:
                best_neg[ev.origin_cluster_id] = (neg, ev)
        logit = prior
        for cluster, (s, ev) in best_pos.items():
            v = self.cfg["direct_llr"] * s
            logit += v
            terms.append({"term": "positive", "origin_cluster_id": cluster, "claim_id": ev.claim_id, "value": round(v, 3)})
        for cluster, (s, ev) in best_neg.items():
            v = -self.cfg["negative_llr"] * s
            logit += v
            terms.append({"term": "negative", "origin_cluster_id": cluster, "claim_id": ev.claim_id, "value": round(v, 3)})
        present = {ev.sub_id for ev in items
                   if ev.role == "prerequisite" and ev.assertion_type not in NOT_PRESENT_TYPES
                   and ev.entity_match not in ("other", "ambiguous") and not ev.copied}
        keys = set(entry.key_prerequisite_ids())
        pre = sum(self.cfg["prerequisite_key_llr"] if p in keys else self.cfg["prerequisite_other_llr"] for p in present)
        pre = min(pre, self.cfg["prerequisite_cap"])
        if pre:
            logit += pre
            terms.append({"term": "prerequisites", "present": sorted(present), "value": round(pre, 3)})
        p = 1.0 / (1.0 + math.exp(-logit))
        return ScoreResult(round(p, 4), round(logit, 4), prior, terms, present)
