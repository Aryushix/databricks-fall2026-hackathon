"""Claim verification engine: audits claims against description, source URLs, and copies."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from ..cleaning import jlist, missing
from .domain_trust import score_domain

NEGATIVE_CUES = re.compile(
    r"\b(?:no|not available|not offered|referral only|referred to|outsourced|discontinued|closed)\b",
    re.IGNORECASE
)


@dataclass
class VerifiedClaim:
    claim_text: str
    category: str
    claim_status: str
    source_trust_score: float
    corroboration_quote: Optional[str] = None
    source_url: Optional[str] = None


def _clean_urls(raw) -> list[str]:
    if isinstance(raw, list):
        return [str(u) for u in raw if u and not missing(u)]
    return [str(u) for u in jlist(raw) if u and not missing(u)]


def verify_facility_claim(
    claim_text: str,
    category: str,
    facility: dict,
) -> VerifiedClaim:
    """Audit a single claim from a facility against its description, source URLs, and status."""
    if missing(claim_text) or not str(claim_text).strip():
        return VerifiedClaim(claim_text=str(claim_text), category=category, claim_status="UNVERIFIED_LISTING", source_trust_score=0.5)

    claim_str = str(claim_text).strip()
    desc = str(facility.get("description") or "")
    urls = _clean_urls(facility.get("source_urls"))
    fac_name = facility.get("name")

    # 1. Compute best source trust score
    best_trust = 0.5
    best_url = None
    if urls:
        for u in urls:
            t = score_domain(u, fac_name)
            if t > best_trust:
                best_trust, best_url = t, u
    else:
        best_url = facility.get("officialWebsite") or None
        if best_url:
            best_trust = score_domain(best_url, fac_name)

    # 2. Check for duplicate / copied listing
    dup_group = facility.get("dup_group_id")
    is_copied = dup_group is not None and not missing(dup_group) and str(dup_group).strip() != ""

    # 3. Check description corroboration or contradiction
    claim_tokens = [tok.lower() for tok in re.findall(r"\b[a-zA-Z]{4,}\b", claim_str)
                    if tok.lower() not in ("with", "care", "unit", "department", "services", "hospital", "clinic")]

    # Also extract sub-stems for compound terms (e.g., 'dialysis' from 'hemodialysis')
    stems = list(claim_tokens)
    for tok in claim_tokens:
        if len(tok) >= 8 and "dialysis" in tok:
            stems.append("dialysis")
        if len(tok) >= 8 and "angio" in tok:
            stems.append("angio")

    status = "UNVERIFIED_LISTING"
    quote = None

    if desc and stems:
        pattern = re.compile(r"([^.\n]*?(?:" + "|".join(re.escape(s) for s in stems) + r")[^.\n]*)", re.IGNORECASE)
        match = pattern.search(desc)
        if match:
            snippet = match.group(1).strip()
            if NEGATIVE_CUES.search(snippet):
                status = "CONTRADICTED"
                best_trust = 0.0
                quote = snippet[:200]
            else:
                status = "CORROBORATED_BY_DESC"
                quote = snippet[:200]

    # If not contradicted and official source trust is high:
    if status != "CONTRADICTED":
        if is_copied:
            status = "COPIED_LISTING"
            best_trust = min(best_trust, 0.40)
        elif best_trust >= 0.90:
            status = "VERIFIED_OFFICIAL"

    return VerifiedClaim(
        claim_text=claim_str,
        category=category,
        claim_status=status,
        source_trust_score=round(best_trust, 2),
        corroboration_quote=quote,
        source_url=best_url,
    )
