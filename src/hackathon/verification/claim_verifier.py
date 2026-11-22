"""A status and a source-trust score for every facility claim (the rows of silver_facility_evidence).

claim_status, checked in this order:
  CONTRADICTED          a sentence of the facility's description that carries the claim also says it's referred,
                        not available, outsourced or discontinued (weight 0.0)
  VERIFIED_OFFICIAL     a line on the facility's own website (cached by tools/facility_crawler.py, domain not shared
                        with other facilities, site names the facility) carries the claim (1.0)
  COPIED_LISTING        the same claim of 6+ words is listed for 3+ differently named facilities (0.3)
  CORROBORATED_BY_DESC  the facility's description carries the claim (0.85)
  UNVERIFIED_LISTING    only in the extracted lists (0.6)

"Carries the claim": the claim's words appear in order (claims of up to 3 words), or 60% of its word trigrams appear
in one line or sentence. corroboration_quote is that line or sentence, verbatim, so every status can be checked.
source_trust_score is the trust of the source behind the status (domain_trust.py): the page's domain for
VERIFIED_OFFICIAL, otherwise the best of the record's source_urls (at most 0.5 for a copied claim; 0 if contradicted).

This complements the treatment verifier (verification/pipeline.py), which reads the same claims per treatment with
look-alike and negation rules. A claim status says what a source carries, not that a service is available today.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Iterable, Optional

import pandas as pd

from .domain_trust import SHARED, score_domain, shared_domains
from .ledger import split_sentences

STATUS_WEIGHT = {"VERIFIED_OFFICIAL": 1.0, "CORROBORATED_BY_DESC": 0.85, "UNVERIFIED_LISTING": 0.6,
                 "COPIED_LISTING": 0.3, "CONTRADICTED": 0.0}
NEGATIVE = re.compile(r"\b(?:referred|refers? (?:to|out)|not available|unavailable|outsourced|discontinued|no longer|"
                      r"not offered|not provided|not done)\b", re.I)
_CAMEL = re.compile(r"([a-z])([A-Z])")
_TOK = re.compile(r"[a-z0-9]+")
COPY_MIN_WORDS, COPY_MIN_FACILITIES, MATCH_SHARE = 6, 3, 0.6


def tokens(text) -> list[str]:
    if not isinstance(text, str):
        return []
    return _TOK.findall(_CAMEL.sub(r"\1 \2", text).lower())


def _grams(toks: list[str]) -> set[tuple]:
    return {tuple(toks[i:i + 3]) for i in range(len(toks) - 2)}


def carries(claim_toks: list[str], text_toks: list[str]) -> bool:
    """Does this line / sentence carry the claim?"""
    if not claim_toks or not text_toks:
        return False
    if len(claim_toks) <= 3:
        return f" {' '.join(claim_toks)} " in f" {' '.join(text_toks)} "
    want = _grams(claim_toks)
    return len(want & _grams(text_toks)) / len(want) >= MATCH_SHARE


def _list(v) -> list[str]:
    if isinstance(v, list):
        return [str(x) for x in v]
    if not isinstance(v, str) or not v.strip():
        return []
    try:
        x = json.loads(v)
        return [str(i) for i in x] if isinstance(x, list) else [v]
    except ValueError:
        return [v]


def explode_claims(facilities: pd.DataFrame, categories=("specialties", "procedure", "equipment", "capability")) -> pd.DataFrame:
    """facilities -> one row per (facility, category, claim), like notebooks/02b_facility_evidence.py."""
    rows = []
    for rec in facilities.to_dict("records"):
        seen = set()
        for cat in categories:
            for text in _list(rec.get(cat)):
                text = text.strip()
                if text and (cat, text.lower()) not in seen:
                    seen.add((cat, text.lower()))
                    rows.append({"facility_id": rec["unique_id"], "facility_name": rec.get("name"),
                                 "category": cat, "evidence_text": text})
    return pd.DataFrame(rows, columns=["facility_id", "facility_name", "category", "evidence_text"])


class ClaimVerifier:
    def __init__(self, facilities: pd.DataFrame, official_lines: Iterable = ()):
        """facilities: the verified facilities table (unique_id, name, description, source_urls,
        officialWebsite_domain). official_lines: ledger.official_claims(...) output (or anything with
        unique_id, text, source_type, source_url); only `official_site_page` lines count."""
        f = facilities.set_index("unique_id")
        self.desc = {uid: [(s, tokens(s)) for s in split_sentences(d)] for uid, d in f["description"].items()
                     if isinstance(d, str)}
        self.urls = {uid: _list(u) for uid, u in f.get("source_urls", pd.Series(dtype=object)).items()}
        self.own = f.get("officialWebsite_domain", pd.Series(dtype=object)).to_dict()
        self.shared = shared_domains(self.urls)
        self.lines: dict[str, list] = defaultdict(list)
        for c in official_lines:
            if getattr(c, "source_type", None) == "official_site_page":
                self.lines[c.unique_id].append((c.text, c.source_url, tokens(c.text)))
        self._index: dict[str, dict] = {}

    def _official(self, uid: str, ctoks: list[str]) -> Optional[tuple[str, str]]:
        lines = self.lines.get(uid)
        if not lines or not ctoks:
            return None
        if len(ctoks) <= 3:
            for text, url, ltoks in lines:
                if carries(ctoks, ltoks):
                    return text, url
            return None
        idx = self._index.get(uid)
        if idx is None:                                       # trigram -> line numbers, built once per facility
            idx = defaultdict(set)
            for i, (_, _, ltoks) in enumerate(lines):
                for g in _grams(ltoks):
                    idx[g].add(i)
            self._index[uid] = idx
        want = _grams(ctoks)
        hits: dict[int, int] = defaultdict(int)
        for g in want:
            for i in idx.get(g, ()):
                hits[i] += 1
        if hits:
            i, n = max(hits.items(), key=lambda kv: kv[1])
            if n / len(want) >= MATCH_SHARE:
                return lines[i][0], lines[i][1]
        return None

    def _best_listing_trust(self, uid: str) -> float:
        scores = [score_domain(u, self.own.get(uid), self.shared) for u in self.urls.get(uid, [])]
        return max(scores) if scores else SHARED

    def verify(self, claims: pd.DataFrame) -> pd.DataFrame:
        """claims: facility_id, facility_name, category, evidence_text -> plus claim_status, source_trust_score,
        corroboration_quote, corroboration_source."""
        names_by_text: dict[str, set] = defaultdict(set)
        toks = [tokens(t) for t in claims["evidence_text"]]
        for t, name in zip(toks, claims["facility_name"].fillna("").str.lower()):
            if len(t) >= COPY_MIN_WORDS:
                names_by_text[" ".join(t)].add(name)
        out = []
        for (uid, text), ctoks in zip(claims[["facility_id", "evidence_text"]].itertuples(index=False), toks):
            status, quote, source, trust = "UNVERIFIED_LISTING", text, "listing", None
            desc_hit = next((s for s, st in self.desc.get(uid, []) if carries(ctoks, st)), None)
            off = self._official(uid, ctoks)
            copied = len(ctoks) >= COPY_MIN_WORDS and len(names_by_text[" ".join(ctoks)]) >= COPY_MIN_FACILITIES
            if desc_hit is not None and NEGATIVE.search(desc_hit):
                status, quote, source, trust = "CONTRADICTED", desc_hit, "description", 0.0
            elif off is not None:
                status, quote, source = "VERIFIED_OFFICIAL", off[0], off[1]
                trust = score_domain(off[1], self.own.get(uid), self.shared)
            elif copied:
                status, trust = "COPIED_LISTING", min(self._best_listing_trust(uid), SHARED)
            elif desc_hit is not None:
                status, quote, source = "CORROBORATED_BY_DESC", desc_hit, "description"
            if trust is None:
                trust = self._best_listing_trust(uid)
            out.append((status, round(float(trust), 2), quote[:300], source))
        res = claims.copy()
        res["claim_status"], res["source_trust_score"], res["corroboration_quote"], res["corroboration_source"] = (
            zip(*out) if out else ([], [], [], []))
        return res
