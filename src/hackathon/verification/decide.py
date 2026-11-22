"""Deterministic status rules, applied in a fixed order. No model decides a status.

  1. CONTRADICTED  a credible NOT_OFFERED / DISCONTINUED / REFERRAL_ONLY / OUTSOURCED_OFF_SITE claim about
                   this facility that is more reliable or more recent than the best positive claim, or
                   unopposed. An undated tie of equal reliability is a conflict, not a contradiction.
  2. CONFIRMED     an OFFERED claim from the facility's own website (decision 6: extracted fields alone
                   never confirm), no unresolved conflict, a plausible facility type, and a score at or
                   above the CONFIRMED threshold.
  3. LIKELY        a direct claim that passes the gates (not copied, facility type plausible, and for a
                   highly specialized treatment at least one key prerequisite), or every key prerequisite
                   documented.
  4. NOT CONFIRMED relevant evidence that does not establish provision.
  5. UNVERIFIED    no relevant evidence. Absence is never treated as negative evidence.
"""

from __future__ import annotations

from .kb import KBEntry
from .records import Evidence
from .scoring import ScoreResult, is_negative

POSITIVE = ("OFFERED", "PARTNER_ON_SITE")
MENTION = ("EDUCATIONAL_OR_GENERIC", "RESEARCH_OR_LISTING", "PERSONNEL_ONLY", "AMBIGUOUS")
NEG_CODE = {"NOT_OFFERED": "NOT_OFFERED", "DISCONTINUED": "DISCONTINUED", "REFERRAL_ONLY": "REFERRAL_ONLY",
            "OUTSOURCED_OFF_SITE": "OUTSOURCED"}


def _rank(ev: Evidence, rel: dict) -> tuple:
    return (rel.get(ev.source_type, 0.0), ev.date or "")


def _q(ev: Evidence, n: int = 140) -> str:
    q = ev.quote if len(ev.quote) <= n else ev.quote[: n - 1] + "…"
    where = "official site" if ev.lineage == "official_site" else f"{ev.field}, page unknown"
    return f"“{q}” ({where})"


def decide(items: list[Evidence], score: ScoreResult, entry: KBEntry, facility_type: str | None,
           identity: dict, cfg: dict) -> dict:
    rel, dcfg, rcfg = cfg["reliability"], cfg["decision"], cfg["review"]
    negative_types = cfg["scoring"]["negative_types"]
    usable = [e for e in items if e.assertion_type != "EXTRACTOR_ARTIFACT"]
    artifacts = len(items) - len(usable)
    direct = [e for e in usable if e.role in ("direct", "ambiguous")]
    prereq = [e for e in usable if e.role == "prerequisite"]
    confusable = [e for e in usable if e.role == "confusable"]
    pos_all = [e for e in direct if e.role == "direct" and e.assertion_type in POSITIVE
               and e.entity_match in ("this", "unnamed", "chain") and not e.copied]
    qualifying = [e for e in pos_all if not e.stale]
    official = [e for e in qualifying if e.source_type == "official_site_page"]
    chain = [e for e in qualifying if e.source_type == "official_chain_page"]
    branch_level = [e for e in qualifying if e.source_type != "official_chain_page"]
    neg = [e for e in direct if is_negative(e, negative_types)]
    present = score.prerequisites_present
    key_ids = entry.key_prerequisite_ids()
    any_key = bool(present & set(key_ids))
    key_met = entry.key_prerequisites_met(present)
    missing = [k for k in key_ids if k not in present]
    plausible = facility_type in entry.plausible_facility_types
    p = score.p_provides
    origins = sorted({e.origin_cluster_id for e in qualifying})
    codes, review, used = [], [], []
    status = None

    # 1. CONTRADICTED
    if neg:
        best_neg = max(neg, key=lambda e: _rank(e, rel))
        best_pos = max(pos_all, key=lambda e: _rank(e, rel)) if pos_all else None
        neg_code = NEG_CODE.get(best_neg.assertion_type, "NOT_OFFERED")
        used.append(best_neg)
        if best_pos is None:
            status, codes = "CONTRADICTED", [neg_code]
        else:
            rn, rp = _rank(best_neg, rel), _rank(best_pos, rel)
            if best_neg.origin_cluster_id != best_pos.origin_cluster_id and (
                    rn[0] > rp[0] or (rn[0] == rp[0] and rn[1] and rp[1] and rn[1] > rp[1])):
                status, codes = "CONTRADICTED", [neg_code]
                review.append("a positive claim exists but a more reliable or more recent negative outranks it")
            elif best_neg.origin_cluster_id != best_pos.origin_cluster_id and (
                    rp[0] > rn[0] or (rp[1] and rn[1] and rp[1] > rn[1])):
                codes.append("CONFLICT_RESOLVED_POSITIVE")
                review.append("a negative claim exists but a more reliable or more recent positive outranks it")
            else:
                status, codes = "NOT CONFIRMED", ["CONFLICT_UNRESOLVED", neg_code]
                review.append("positive and negative claims of equal standing conflict")
                used.append(best_pos)

    # 2. CONFIRMED
    if status is None and official:
        if plausible and p >= dcfg["confirmed_threshold"]:
            status = "CONFIRMED"
            codes.append("DIRECT_OFFICIAL")
            if len(origins) >= 2:
                codes.append("DIRECT_MULTI_ORIGIN")
            used.extend(sorted(official, key=lambda e: -e.weight)[:3])
        elif plausible and dcfg["confirmed_threshold"] - p <= dcfg["near_threshold_margin"]:
            review.append(f"eligible for CONFIRMED but the score {p:.2f} is just under the threshold "
                          f"{dcfg['confirmed_threshold']:.2f}")

    # 3. LIKELY
    if status is None:
        gate_fail = []
        if qualifying:
            if not plausible:
                gate_fail.append("IMPLAUSIBLE_FACILITY_TYPE")
            if entry.specialization == "highly_specialized" and not official and not any_key:
                gate_fail.append("PREREQS_MISSING")
            if not branch_level and chain:
                codes.append("CHAIN_SITE_ONLY")
            if not gate_fail:
                status = "LIKELY"
                codes.append("DIRECT_OFFICIAL" if official else "DIRECT_SINGLE_ORIGIN")
                if any(e.assertion_type == "PARTNER_ON_SITE" for e in qualifying):
                    codes.append("PARTNER_ON_SITE")
                used.extend(sorted(qualifying, key=lambda e: -e.weight)[:3])
            else:
                codes.extend(gate_fail)
                used.extend(sorted(qualifying, key=lambda e: -e.weight)[:2])
                if "PREREQS_MISSING" in gate_fail and rcfg.get("prerequisites_missing"):
                    review.append("a direct claim appears without any of the treatment's key prerequisites")
        if status is None and key_met and (not gate_fail or gate_fail == ["IMPLAUSIBLE_FACILITY_TYPE"]):
            status = "LIKELY"
            codes.append("PREREQS_MET")
            if gate_fail:
                review.append(f"facility type '{facility_type}' does not usually provide this treatment")
        if key_met and "PREREQS_MET" not in codes:
            codes.append("PREREQS_MET")

    # 4. NOT CONFIRMED / 5. UNVERIFIED
    if status is None:
        reasons = []
        if any(e.copied for e in direct if e.assertion_type in POSITIVE):
            reasons.append("COPIED_LISTING")
        if any(e.entity_match == "other" for e in direct + prereq):
            reasons.append("OTHER_ENTITY")
        if any(e.entity_match == "ambiguous" for e in direct):
            reasons.append("ENTITY_AMBIGUOUS")
        if any(e.assertion_type == "PLANNED_OR_COMING_SOON" for e in direct):
            reasons.append("PLANNED")
        if any(e.assertion_type == "OUTREACH_CAMP" for e in direct):
            reasons.append("CAMP_ONLY")
        if any(e.assertion_type in MENTION for e in direct):
            reasons.append("MENTION_ONLY")
        if any(e.stale for e in pos_all):
            reasons.append("STALE")
        if any(e.assertion_type in NEG_CODE and not is_negative(e, negative_types) for e in direct):
            reasons.append("PARTIAL_NEGATIVE")      # a child, a subgroup, or another facility
        if confusable and not direct:
            reasons.append("CONFUSABLE_ONLY")
        if any_key and not key_met:
            reasons.append("PREREQS_PARTIAL")
        relevant = bool(direct or confusable or codes)   # the treatment itself is mentioned
        if relevant:
            status = "NOT CONFIRMED"
            codes.extend(r for r in reasons if r not in codes)
            used.extend([e for e in direct if e not in used][:3] or confusable[:2])
        else:
            status = "UNVERIFIED"
            codes.append("NO_EVIDENCE")
            if any_key:
                codes.append("PREREQS_PARTIAL")      # e.g. a cath lab, but no claim about the procedure
            elif prereq:
                codes.append("NON_KEY_PREREQS_ONLY")
    if artifacts:
        codes.append("ARTIFACT_IGNORED")

    # review flags
    direct_used = [e for e in used if e.role in ("direct", "ambiguous")]
    if status in ("LIKELY", "CONTRADICTED") and direct_used and rcfg.get("unidentified_source_only") and all(
            e.source_type == "extracted_record" for e in direct_used):
        review.append("the only direct evidence is an extracted claim whose source page is unknown")
    if status in ("CONFIRMED", "LIKELY") and entry.specialization == "highly_specialized" and rcfg.get("highly_specialized"):
        review.append("highly specialized treatment")
    if status in ("CONFIRMED", "LIKELY", "CONTRADICTED") and identity.get("ambiguity_reasons"):
        review.append("facility identity is uncertain: " + "; ".join(identity["ambiguity_reasons"]))
    if any(e.entity_match == "other" for e in direct) and status != "UNVERIFIED":
        review.append("a claim in this record is about a different facility, so its unnamed claims may be too")
    if rcfg.get("affiliate") and any(e.assertion_type == "PARTNER_ON_SITE" for e in used):
        review.append("evidence points to a partner or visiting specialist")
    if rcfg.get("rule_llm_disagreement") and any(e.disagreement for e in items):
        review.append("the rule pass and the model disagree on a claim")
    if status == "CONFIRMED" and not key_met:
        review.append("CONFIRMED without every key prerequisite documented")

    if status in ("CONFIRMED", "LIKELY"):
        confidence = p
    elif status in ("CONTRADICTED", "NOT CONFIRMED"):
        confidence = round(1.0 - p, 4)
    else:
        confidence = None
    return {
        "verification_status": status,
        "reason_codes": codes,
        "confidence_score": confidence,
        "requires_manual_review": bool(review),
        "review_reasons": review,
        "missing_prerequisites": missing,
        "prerequisites_present": sorted(present),
        "independent_origin_count": len(origins),
        "used": used,
        "reasoning_summary": summarize(status, codes, used, direct, confusable, present, missing, entry, artifacts),
    }


def summarize(status, codes, used, direct, confusable, present, missing, entry, artifacts) -> str:
    """Built only from the evidence and codes, so it cannot claim anything the evidence doesn't hold."""
    parts = [f"{status} for {entry.canonical_name} ({', '.join(codes)})."]
    pos = [e for e in used if e.role in ("direct", "ambiguous") and e.assertion_type in POSITIVE + MENTION]
    neg = [e for e in used if e.assertion_type in NEG_CODE]
    if pos:
        parts.append(f"Strongest positive evidence: {_q(pos[0])}.")
    if neg:
        parts.append(f"Negative evidence: {_q(neg[0])}.")
    others = [e for e in used if e not in pos and e not in neg and e.role != "confusable"]
    if others and status in ("NOT CONFIRMED",):
        parts.append(f"Evidence that does not count as provision: {_q(others[0])} [{others[0].assertion_type}"
                     f"{', copied listing' if others[0].copied else ''}"
                     f"{', about another facility' if others[0].entity_match == 'other' else ''}].")
    if confusable and status in ("NOT CONFIRMED", "UNVERIFIED"):
        parts.append(f"Confusable mention, not counted: {_q(confusable[0])}.")
    if present:
        parts.append("Prerequisites documented: " + ", ".join(sorted(present)) + ".")
    if missing and status != "UNVERIFIED":
        parts.append("Key prerequisites not documented: " + ", ".join(missing) + ".")
    if artifacts:
        parts.append(f"{artifacts} extractor note(s) about missing content were ignored.")
    return " ".join(parts)
