"""The claim ledger: every facility split once into atomic claims, with lineage and a content hash.

Two lineages exist today:
  * vf_extraction  - the Virtue Foundation's extracted fields. All of a record's fields come from one
                     extraction run over its page list, so they form ONE origin cluster: vf:<unique_id>.
  * official_site  - lines of the facility's own website, cached by tools/facility_crawler.py in
                     crawl.sqlite. Each domain is one origin cluster: official:<domain>.
"""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
import zlib
from collections import Counter
from pathlib import Path

import pandas as pd

from .. import cleaning
from .entities import FacilityProfile, owns_site
from .records import Claim

log = logging.getLogger(__name__)

LIST_FIELDS = ("capability", "procedure", "equipment")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\"'])|\n+")
_BLOG_PATH = re.compile(r"/(?:blog|blogs|news|article|articles|faq|faqs|health-tips|what-is|press)\b", re.I)


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def content_hash(text: str) -> str:
    return hashlib.sha1(normalize(text).encode("utf-8")).hexdigest()[:16]


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(text or "") if s and len(s.strip()) >= 3]


def _date(v):
    return None if cleaning.missing(v) or not v else str(v)[:10]


def facility_claims(row: dict) -> list[Claim]:
    """Every claim of one facility record (a row of silver_facilities_verified or its CSV export)."""
    uid = row["unique_id"]
    origin = f"vf:{uid}"
    src_date = _date(row.get("recency_of_page_update"))
    out = []

    def add(field, i, text):
        text = cleaning.clean_text(text) if isinstance(text, str) else None
        if text:
            out.append(Claim(f"{uid}:{field}:{i}", uid, field, i, text, content_hash(text), "vf_extraction",
                             origin, "extracted_record", None, src_date, None))

    for i, s in enumerate(split_sentences(row.get("description") or "")):
        add("description", i, s)
    for f in LIST_FIELDS:
        for i, item in enumerate(cleaning.jlist(row.get(f))):
            add(f, i, item)
    for i, item in enumerate(cleaning.jlist(row.get("specialties"))):
        add("specialties", i, item)
    return out


def build_ledger(facilities: pd.DataFrame) -> list[Claim]:
    claims = []
    for row in facilities.to_dict("records"):
        claims.extend(facility_claims(row))
    log.info("ledger: %d claims from %d facilities", len(claims), len(facilities))
    return claims


# --------------------------------------------------------------------------- official pages
def _page_lines(html: str) -> list[tuple[str, str | None]]:
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:  # pragma: no cover - only without the laptop tools installed
        raise ImportError("Reading crawl.sqlite needs beautifulsoup4 (pip install -r tools/requirements.txt)") from exc
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript", "svg"]):
        t.decompose()
    nav = set()
    for el in soup.find_all(["nav", "header", "footer"]):
        nav |= {ln.strip() for ln in el.get_text("\n").split("\n") if ln.strip()}
    seen, out = set(), []
    for ln in soup.get_text("\n").split("\n"):
        ln = re.sub(r"\s+", " ", ln).strip()
        if 3 <= len(ln) <= 400 and ln not in seen:
            seen.add(ln)
            out.append((ln, "navigation" if ln in nav else None))
    return out


def chain_domains(facilities: pd.DataFrame, min_facilities: int = 3) -> set[str]:
    """Domains whose pages can't be pinned to one branch: shared by min_facilities+ records, or by
    records in two or more cities (a chain, or one name's website attached to several namesakes)."""
    df = facilities[["officialWebsite_domain", "address_city"]].dropna(subset=["officialWebsite_domain"])
    g = df.groupby("officialWebsite_domain")
    n = g.size()
    cities = g.address_city.apply(lambda s: s.fillna("").str.lower().str.strip().nunique())
    return set(n[(n >= min_facilities) | (cities >= 2)].index)


def official_claims(db_path: str | Path, facilities: pd.DataFrame, min_chain: int = 3) -> list[Claim]:
    """Lines of each facility's own cached website pages, as claims with page-level provenance."""
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    results = pd.read_sql("SELECT unique_id, website FROM results", db)
    pages = pd.read_sql("SELECT url, fetched_at, html FROM pages WHERE status = 200 AND html IS NOT NULL", db)
    db.close()
    pages["domain"] = pages.url.map(cleaning.domain_of)
    results["domain"] = results.website.map(cleaning.domain_of)
    known = set(facilities.unique_id)
    results = results[results.unique_id.isin(known) & results.domain.isin(set(pages.domain))]
    chains = chain_domains(facilities, min_chain)
    lines_by_url = {}
    for rec in pages.to_dict("records"):
        html = zlib.decompress(rec["html"]).decode("utf-8", "replace")
        lines_by_url[rec["url"]] = (rec, _page_lines(html))
    names = facilities.set_index("unique_id")
    text_by_domain = {}
    for url, (rec, lines) in lines_by_url.items():
        text_by_domain.setdefault(rec["domain"], []).extend(t for t, _ in lines[:400])
    cities = frozenset(c for c in facilities["address_city"].dropna().str.lower().str.replace(r"[^a-z]", "", regex=True)
                       if len(c) >= 5)
    out, unmatched = [], 0
    for uid, dom in zip(results.unique_id, results.domain):
        prof = FacilityProfile.build(uid, names.at[uid, "name"], names.at[uid, "address_city"])
        if not owns_site(prof, dom, " ".join(text_by_domain.get(dom, [])), cities):
            stype, unmatched = "official_site_unmatched", unmatched + 1   # the website names someone else
        elif dom in chains:
            stype = "official_chain_page"
        else:
            stype = "official_site_page"
        n = 0
        for url, (rec, lines) in lines_by_url.items():
            if rec["domain"] != dom:
                continue
            blog = bool(_BLOG_PATH.search(url))
            for text, ctx in lines:
                out.append(Claim(f"{uid}:official:{n}", uid, "official_page", n, text, content_hash(text),
                                 "official_site", f"official:{dom}", stype, url, None,
                                 _date(rec["fetched_at"]), "blog" if blog else ctx))
                n += 1
    log.info("official pages: %d lines for %d facilities (%d chain domains, %d sites that don't name the facility)",
             len(out), results.unique_id.nunique(), len(chains & set(results.domain)), unmatched)
    return out


def shared_content_sizes(facilities: pd.DataFrame) -> dict[str, int]:
    """For each facility, how many facilities share its source_content_id (1 = not shared)."""
    counts = Counter(facilities["source_content_id"].fillna(""))
    return {u: (counts[c] if c else 1) for u, c in zip(facilities.unique_id, facilities["source_content_id"].fillna(""))}
