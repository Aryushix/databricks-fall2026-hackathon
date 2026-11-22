"""Multi-pillar semantic, vector embedding, and KB service matcher within a specified radius (e.g. 150 km)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import yaml

from ..cleaning import haversine_km, jlist
from ..verification.claim_verifier import verify_facility_claim
from .contract import EvidenceQuote, MatchedNeed, Recommendation
from .embeddings import compute_pillar_similarity, compute_semantic_similarity
from .query_expansion import expand_service_query

REPO_ROOT = Path(__file__).resolve().parents[3]
SERVICES_CONFIG = REPO_ROOT / "configs" / "services.yaml"


def _stem(tok: str) -> str:
    tok = tok.lower()
    for suffix in ("ology", "ologist", "ological", "ectomy", "plasty", "iatry", "iatric", "iatrist", "pathy", "therapy", "scopic", "scopy", "ical", "itis", "ic", "al", "ia", "y", "s"):
        if tok.endswith(suffix) and len(tok) - len(suffix) >= 4:
            return tok[:-len(suffix)]
    return tok


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"\b[a-zA-Z]{3,}\b", text.lower())) - {"and", "the", "for", "with", "unit", "care", "services"}


def _match_list(required_items: list[str], facility_items: list[str]) -> tuple[float, list[str]]:
    """Compare required items against facility items using token and root-stem overlap."""
    if not required_items or not facility_items:
        return 0.0, []

    matched = []
    total_req = len(required_items)
    score_sum = 0.0

    for req in required_items:
        req_toks = _tokens(req)
        req_stems = {_stem(t) for t in req_toks}
        best_item_score = 0.0
        best_item = None
        for fac_item in facility_items:
            fac_str = str(fac_item).lower()
            fac_toks = _tokens(fac_str)
            fac_stems = {_stem(t) for t in fac_toks}
            if not req_toks or not fac_toks:
                continue

            # Check direct overlap or stem overlap
            direct_overlap = len(req_toks & fac_toks)
            stem_overlap = sum(1 for rs in req_stems if any(rs == fs or rs in fs or fs in rs for fs in fac_stems))
            overlap = max(direct_overlap, stem_overlap)

            # Also check if query term is a substring in the item
            if req.lower() in fac_str:
                overlap = max(overlap, len(req_toks))

            if overlap > 0:
                s = overlap / len(req_toks)
                if s > best_item_score:
                    best_item_score = s
                    best_item = fac_item
        if best_item_score >= 0.5:
            score_sum += min(best_item_score, 1.0)
            if best_item and str(best_item) not in matched:
                matched.append(str(best_item))

    return round(score_sum / total_req, 3), matched


class ServiceMatcher:
    def __init__(self, services_cfg_path: str | Path = SERVICES_CONFIG):
        self.cfg = yaml.safe_load(Path(services_cfg_path).read_text(encoding="utf-8"))
        self.services = self.cfg.get("services", {})

    def match_facilities(
        self,
        store,
        service_id: str,
        lat: float,
        lon: float,
        radius_km: float = 150.0,
        max_results: int = 5,
        use_llm_expansion: bool = True,
    ) -> list[Recommendation]:
        """Find facilities matching the service within radius_km, using hybrid semantic + KB search."""
        fac = store.facilities()
        svc = self.services.get(service_id)

        # Fallback 1: match by name or synonym across known services
        if not svc:
            for sid, sdata in self.services.items():
                if sid.lower() == service_id.lower() or sdata.get("name", "").lower() == service_id.lower():
                    svc = sdata
                    service_id = sid
                    break

        # Clinical Query Expansion (LLM or cached/heuristic)
        expansion = expand_service_query(service_id, use_llm=use_llm_expansion)

        if not svc:
            svc_name = expansion.service_name or service_id.replace("_", " ").title()
            svc = {
                "name": svc_name,
                "category": "Custom Healthcare Service",
                "kb_id": None,
                "requirements": {
                    "equipment": expansion.equipment or [service_id],
                    "procedures": expansion.procedures or [service_id],
                    "specialties": expansion.specialties or [service_id],
                    "capabilities": expansion.capabilities or [service_id]
                }
            }

        reqs = svc.get("requirements", {})
        req_equip = list(dict.fromkeys(reqs.get("equipment", []) + expansion.equipment))
        req_proc = list(dict.fromkeys(reqs.get("procedures", []) + expansion.procedures))
        req_spec = list(dict.fromkeys(reqs.get("specialties", []) + expansion.specialties))
        req_cap = list(dict.fromkeys(reqs.get("capabilities", []) + expansion.capabilities))
        all_query_terms = [service_id, svc.get("name", "")] + expansion.clinical_synonyms

        # 1. Geospatial distance check (150 km)
        dists = haversine_km(lat, lon, fac.latitude.to_numpy(float), fac.longitude.to_numpy(float))
        has_xy = ~np.isnan(dists)
        in_range_mask = has_xy & (dists <= radius_km)

        candidate_indices = np.where(in_range_mask)[0]
        if len(candidate_indices) == 0:
            return []

        # 2. Check if a KB exists for this service or its expanded synonyms
        kb_id = svc.get("kb_id")
        kb_records = {}
        if kb_id:
            kb_records = {r["hospital_id"]: r for r in store.records_for(kb_id)}
        else:
            # Check if any synonym matches a known KB in store
            for syn in all_query_terms:
                syn_clean = syn.lower().replace(" ", "_")
                if syn_clean in store.kb_ids():
                    kb_records = {r["hospital_id"]: r for r in store.records_for(syn_clean)}
                    kb_id = syn_clean
                    break

        results = []

        for idx in candidate_indices:
            hid = fac.index[idx]
            d = dists[idx]
            row = fac.iloc[idx].to_dict()

            # Check if KB match exists
            kb_rec = kb_records.get(hid)
            kb_status = kb_rec.get("verification_status") if kb_rec else None

            # Disqualify CONTRADICTED
            if kb_status == "CONTRADICTED":
                continue

            # Extract facility claims
            f_equip = [str(x) for x in jlist(row.get("equipment")) if x]
            f_proc = [str(x) for x in jlist(row.get("procedure")) if x]
            f_spec = [str(x) for x in jlist(row.get("specialties")) if x]
            f_cap = [str(x) for x in jlist(row.get("capability")) if x]

            # 3. Dense Vector / Semantic 4-Pillar matching
            s_proc_sem, m_proc_sem = compute_pillar_similarity(req_proc, f_proc)
            s_equip_sem, m_equip_sem = compute_pillar_similarity(req_equip, f_equip)
            s_spec_sem, m_spec_sem = compute_pillar_similarity(req_spec, f_spec)
            s_cap_sem, m_cap_sem = compute_pillar_similarity(req_cap, f_cap)

            # Also check lexical matching to guarantee exact keyword robustness
            s_proc_lex, m_proc_lex = _match_list(req_proc, f_proc)
            s_equip_lex, m_equip_lex = _match_list(req_equip, f_equip)
            s_spec_lex, m_spec_lex = _match_list(req_spec, f_spec)
            s_cap_lex, m_cap_lex = _match_list(req_cap, f_cap)

            # Combined pillar scores (max of semantic and lexical)
            s_proc = max(s_proc_sem, s_proc_lex)
            s_equip = max(s_equip_sem, s_equip_lex)
            s_spec = max(s_spec_sem, s_spec_lex)
            s_cap = max(s_cap_sem, s_cap_lex)

            m_proc = list(dict.fromkeys(m_proc_sem + m_proc_lex))
            m_equip = list(dict.fromkeys(m_equip_sem + m_equip_lex))
            m_spec = list(dict.fromkeys(m_spec_sem + m_spec_lex))
            m_cap = list(dict.fromkeys(m_cap_sem + m_cap_lex))

            # Direct synonym-to-specialties check
            direct_spec_match = 0.0
            for term in all_query_terms:
                for fac_sp in f_spec:
                    sim = compute_semantic_similarity(term, fac_sp)
                    if sim > direct_spec_match:
                        direct_spec_match = sim
                        if fac_sp not in m_spec:
                            m_spec.append(fac_sp)

            best_pillar = max(s_proc, s_equip, s_spec, s_cap, direct_spec_match)
            weighted_pillar = round(0.35 * s_proc + 0.30 * s_equip + 0.20 * s_spec + 0.15 * s_cap, 3)
            semantic_score = max(weighted_pillar, round(best_pillar * 0.75, 3))

            # Audit claims for trust
            all_matched = m_proc + m_equip + m_spec + m_cap
            verified_claims = [verify_facility_claim(c, "matched_claim", row) for c in all_matched[:4]]

            # Check for contradiction in text
            if any(vc.claim_status == "CONTRADICTED" for vc in verified_claims):
                continue

            avg_trust = (
                np.mean([vc.source_trust_score for vc in verified_claims])
                if verified_claims
                else 0.5
            )

            # Determine Tier and Confidence Score
            if kb_status == "CONFIRMED":
                tier = 1
                conf = kb_rec.get("confidence_score") or 0.95
                status_label = "CONFIRMED"
                match_list_type = "verified"
            elif kb_status == "LIKELY":
                tier = 2
                conf = kb_rec.get("confidence_score") or 0.75
                status_label = "LIKELY"
                match_list_type = "verified"
            elif semantic_score >= 0.25:
                tier = 3
                conf = round(float(semantic_score * avg_trust), 2)
                status_label = "SEMANTIC_MATCH"
                match_list_type = "verified" if conf >= 0.50 else "unverified_fallback"
            else:
                continue

            # Direct evidence quotes
            quotes = []
            if kb_rec and kb_rec.get("direct_evidence"):
                for de in kb_rec["direct_evidence"][:2]:
                    quotes.append(EvidenceQuote(
                        quote=de.get("quote", ""),
                        field=de.get("field", "capability"),
                        source_type=de.get("source_type", "listing"),
                        source_url=de.get("source_url")
                    ))
            elif verified_claims:
                for vc in verified_claims[:2]:
                    if vc.corroboration_quote:
                        quotes.append(EvidenceQuote(
                            quote=vc.corroboration_quote,
                            field="description",
                            source_type="corroborated_desc",
                            source_url=vc.source_url
                        ))
                    else:
                        quotes.append(EvidenceQuote(
                            quote=f"Listed {vc.claim_text} in facility capabilities.",
                            field="listing",
                            source_type="facility_listing",
                            source_url=vc.source_url
                        ))

            matched_need = MatchedNeed(
                care_need=service_id,
                role="required",
                kb_id=kb_id or service_id,
                status=status_label,
                confidence=conf,
                confidence_basis="heuristic_v1" if kb_status else "semantic_v2_hybrid",
                evidence=quotes
            )

            # Warnings
            warnings = []
            loc_status = row.get("location_status")
            if loc_status in ("partly_verified", "conflicting"):
                warnings.append("Location coordinates are approximate (PIN area).")
            if avg_trust < 0.6:
                warnings.append("Evidence extracted primarily from directory listings; call ahead to confirm.")

            # Summary explanation
            expl = f"{row.get('name')} is located {d:.1f} km away. "
            if status_label in ("CONFIRMED", "LIKELY"):
                expl += f"Documented verified capability for {svc['name']} ({status_label})."
            else:
                expl += f"Matches requirements for {svc['name']} based on listed procedures, equipment, and specialties."

            results.append({
                "sort_key": (tier, -conf, d),
                "rec": Recommendation(
                    rank=1,
                    list=match_list_type,
                    hospital_id=str(hid),
                    hospital_name=row.get("name") or str(hid),
                    facility_type=row.get("facilityTypeId") or "hospital",
                    latitude=float(row.get("latitude") or 0.0),
                    longitude=float(row.get("longitude") or 0.0),
                    straight_line_distance_km=round(float(d), 1),
                    listed_phone=row.get("officialPhone"),
                    listed_phone_note="facility direct line" if row.get("officialPhone") else "no phone listed",
                    capability_match=(status_label in ("CONFIRMED", "LIKELY", "SEMANTIC_MATCH")),
                    matched_care_needs=[matched_need],
                    explanation=expl,
                    warnings=warnings
                )
            })

        # Sort: tier -> highest confidence -> shortest distance
        results.sort(key=lambda x: x["sort_key"])

        final_recs = []
        for i, item in enumerate(results[:max_results]):
            r = item["rec"]
            r.rank = i + 1
            final_recs.append(r)

        return final_recs
