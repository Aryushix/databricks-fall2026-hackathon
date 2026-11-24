"""How much to trust a source URL, before reading what it says.

    1.0   the facility's own website, or an institutional domain (.gov.in, .nic.in, .edu.in, .ac.in, .edu, .org,
          .org.in) that isn't shared by several facilities
    0.6   any other domain we can't place
    0.5   a domain shared by `SHARED_MIN` or more facilities (a chain, a directory, a government portal listing many
          hospitals): it describes many places, not this one
    0.45  a known aggregator or social directory (Practo, JustDial, Facebook ...: cleaning.NON_OFFICIAL_DOMAINS)

An institutional domain counts as official only while it isn't shared: a .gov.in portal that lists fifty hospitals
says nothing certain about any one of them. This is a source score, not a claim score: what the page actually says is
checked separately (claim_verifier.py).
"""

from __future__ import annotations

from typing import Iterable, Optional

from ..cleaning import domain_of, is_non_official

OFFICIAL, GENERIC, SHARED, AGGREGATOR = 1.0, 0.6, 0.5, 0.45
INSTITUTIONAL_SUFFIXES = (".gov.in", ".nic.in", ".edu.in", ".ac.in", ".edu", ".org", ".org.in", ".res.in")
SHARED_MIN = 3


def score_domain(url: Optional[str], own_domain: Optional[str] = None, shared_domains: Iterable[str] = ()) -> float:
    dom = domain_of(url) if url else None
    if not dom:
        return 0.0
    if is_non_official(dom):
        return AGGREGATOR
    if dom in set(shared_domains):
        return SHARED
    own = domain_of(own_domain) if own_domain else None
    if own and (dom == own or dom.endswith("." + own) or own.endswith("." + dom)):
        return OFFICIAL
    if dom.endswith(INSTITUTIONAL_SUFFIXES):
        return OFFICIAL
    return GENERIC


def shared_domains(domains_by_facility: dict[str, Iterable[str]], min_facilities: int = SHARED_MIN) -> set[str]:
    """Domains that appear among the sources of `min_facilities` or more facilities (aggregators excluded)."""
    seen: dict[str, set[str]] = {}
    for fid, urls in domains_by_facility.items():
        for u in urls:
            d = domain_of(u)
            if d and not is_non_official(d):
                seen.setdefault(d, set()).add(fid)
    return {d for d, f in seen.items() if len(f) >= min_facilities}
