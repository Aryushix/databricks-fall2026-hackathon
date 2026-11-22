"""Source typing for a facility's cited URLs, and the record-level identity checks.

Extracted claims are not tied to a page, so a claim's own source stays "extracted_record". What we can
say about the record as a whole (which kinds of pages it was built from, whether its website looks like
someone else's) is reported beside the verdict, never used to upgrade a claim.
"""

from __future__ import annotations

import re
from collections import Counter

from .. import cleaning
from .entities import acronym, distinctive

_GOV = re.compile(r"(\.gov\.in$|\.nic\.in$|\.gov$|nabh\.co|pmjay|^nhm\.|abdm|mohfw|cghs)")
_LIT = re.compile(r"(ncbi\.nlm|pubmed|^pmc\.|researchgate|springer|sciencedirect|elsevier|wiley|bmj\.com|"
                  r"nature\.com|frontiersin|cureus|lww\.com|tandfonline|sagepub|jamanetwork|thelancet|"
                  r"medrxiv|europepmc|journal|ijmr|jcdr|scribd)")
_SOCIAL = re.compile(r"(facebook|instagram|linkedin|youtube|twitter|^x\.com$|wa\.me|linktr|threads\.net|pinterest)")
_NEWS = re.compile(r"(timesofindia|thehindu|hindustantimes|ndtv|indianexpress|tribuneindia|deccanherald|"
                   r"livemint|economictimes|business-standard|telegraphindia|firstpost|indiatoday|news)")


def url_type(url: str, own_domain: str | None) -> str:
    d = cleaning.domain_of(url) or ""
    if own_domain and (d == own_domain or d.endswith("." + own_domain)):
        return "own_domain"
    if _GOV.search(d):
        return "government"
    if _LIT.search(d):
        return "literature"
    if _SOCIAL.search(d):
        return "social"
    if cleaning.is_non_official(d):
        return "known_directory"
    if _NEWS.search(d):
        return "news"
    return "unidentified"


def official_domain(row: dict) -> str | None:
    d = row.get("officialWebsite_domain")
    if cleaning.missing(d) or not d or cleaning.is_non_official(d):
        return None
    return d


def record_sources(row: dict) -> dict:
    """Counts of the record's cited URLs by type, plus its own-domain URLs (at most 5)."""
    own = official_domain(row)
    urls = cleaning.jlist(row.get("source_urls"))
    types = [url_type(u, own) for u in urls]
    own_urls = [u for u, t in zip(urls, types) if t == "own_domain"][:5]
    return {"n_urls": len(urls), "by_type": dict(Counter(types)), "own_domain_urls": own_urls,
            "note": "Extracted claims are not linked to a specific URL; these are the record's sources."}


def identity_checks(row: dict, shared_content_size: int) -> dict:
    """Signals that the record may mix facilities or point to the wrong place."""
    flags = set((row.get("dq_flags") or "").split(";")) - {""}
    own = official_domain(row)
    name = row.get("name") or ""
    acr = (acronym(name) or "").lower()
    compact = (own or "").replace("-", "")
    website_mismatch = (bool(own) and bool(distinctive(name))
                        and not any(t in compact for t in distinctive(name))
                        and not (acr and acr in compact))
    checks = {
        "location_status": row.get("location_status"),
        "location_confidence": row.get("location_confidence"),
        "location_evidence": row.get("location_evidence"),
        "website_domain": own,
        "website_name_mismatch": website_mismatch,
        "possible_duplicate": "possible_duplicate" in flags,
        "shared_source_content_facilities": int(shared_content_size),
        "phone_shared_across_facilities": "phone_shared_across_facilities" in flags,
    }
    reasons = []
    if checks["location_status"] == "conflicting":
        reasons.append("location fields point to different places")
    if website_mismatch:
        reasons.append(f"website domain {own} shares no distinctive word with the name")
    if checks["possible_duplicate"]:
        reasons.append("possible duplicate facility record")
    if shared_content_size >= 3:
        reasons.append(f"extracted from content shared with {shared_content_size - 1} other facilities")
    checks["ambiguity_reasons"] = reasons
    return checks
