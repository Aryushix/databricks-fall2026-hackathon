"""Domain trust scoring for facility claims and source URLs."""

from __future__ import annotations

import re
from typing import Optional

from ..cleaning import domain_of, is_non_official, missing


def score_domain(url: Optional[str], facility_name: Optional[str] = None) -> float:
    """Score the credibility of a source URL between 0.0 and 1.0."""
    if missing(url) or not url:
        return 0.5

    dom = domain_of(str(url))
    if not dom:
        return 0.5

    # Check non-official aggregators and social platforms
    if is_non_official(dom):
        if any(dom == d or dom.endswith("." + d) for d in ("bit.ly", "linktr.ee", "wa.me", "facebook.com", "instagram.com", "x.com", "twitter.com")):
            return 0.35
        # Directory / aggregator site
        return 0.50

    # Government and academic institutional domains
    if dom.endswith(".gov.in") or dom.endswith(".nic.in"):
        return 1.0
    if dom.endswith(".edu.in") or dom.endswith(".ac.in"):
        return 0.95

    # Match domain with facility name if available
    if facility_name and isinstance(facility_name, str):
        tokens = [t.lower() for t in re.findall(r"\b[a-zA-Z]{4,}\b", facility_name) if t.lower() not in ("hospital", "clinic", "center", "centre", "healthcare", "medical")]
        if any(tok in dom for tok in tokens):
            return 0.95

    if dom.endswith(".org") or dom.endswith(".org.in"):
        return 0.90

    return 0.75
