"""Assertion classification: what a claim says about a treatment it mentions.

Three classifiers share one interface, so a fine-tuned model can replace the LLM later:
  * RuleClassifier  - cue phrases scoped to the clause around the treatment mention (ConText-style).
                      Cheap, transparent, and always run.
  * LLMClassifier   - a Databricks-served model with schema-constrained JSON, one claim at a time. Its
                      quote must be an exact substring of the claim or the output is rejected.
  * DualPass        - runs both, keeps the model's label when it passes the quote check, and flags
                      every disagreement for review.
The classifier only labels claims. Entity matching runs in code (entities.py) and the verdict is
decided by deterministic rules (decide.py); no model decides a status.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import asdict, replace
from datetime import date
from pathlib import Path
from typing import Protocol

from .records import ASSERTION_TYPES, Claim, Classification, ConceptMatch

log = logging.getLogger(__name__)

THIS_YEAR = date.today().year
OLD_YEARS = 5
_YEAR = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")
_CLAUSE_END = re.compile(r"[;.!?]|\bbut\b|\bhowever\b|\bwhereas\b", re.I)

_ARTIFACT = re.compile(
    r"\b(?:provided|given|supplied|available|source) (?:content|text|information|page|material)s?\b|"
    r"^\s*no (?:explicit |specific |clear )?[\w ]{0,40}?(?:listed|mentioned|specified|found|stated)\b", re.I)
_DISCONTINUED = re.compile(r"\b(?:discontinu\w+|no longer|ceased|non[- ]?functional|defunct|closed down|shut down|"
                           r"to be shut|suspended|stopped)\b", re.I)
_RESTARTED = re.compile(r"\b(?:restarted|resumed|re-?started|revived|reopened|now (?:provided|available|offered|functional))\b",
                        re.I)
_PLANNED = re.compile(r"\b(?:planned|plans? to|proposed|upcoming|coming soon|will be (?:started|launched|set up|"
                      r"introduced|available|commissioned)|to be (?:started|set up|commissioned|launched|installed)|"
                      r"under construction|being (?:built|set up|established)|soon|shortly|in the pipeline)\b", re.I)
_OUTSOURCED = re.compile(r"\b(?:outsourc\w*|tie[- ]?up|tied up|(?:through|via|by) (?:an? )?(?:external|outside)|outside collaboration|"
                         r"sent (?:out|outside))\b", re.I)
_SCHEME = re.compile(r"\b(?:esic|cghs|echs|pm-?jay|ayushman|insurance|tpa|cashless|empanel\w*|railway|panel)\b", re.I)
_NEG_BEFORE = re.compile(r"(?:\bno\b|\bnot\b|\black(?:s|ing)?(?: of)?\b|\babsence of\b|"
                         r"\bdoes(?:n't| not) (?:offer|provide|have|perform|do)\b|\bnon[- ]availability of\b)"
                         r"[^;.]{0,45}$", re.I)
_NEG_AFTER = re.compile(r"^[^;.]{0,30}?\b(not (?:available|offered|provided|done|performed|functional|present)|"
                        r"unavailable|is not available|are not available)\b", re.I)
_AFTERCARE = re.compile(r"\bpost[- ](?:operative |procedure )?(?:p\.?t\.?c\.?a|pci|angioplast\w*|cabg|bypass|"
                        r"transplant\w*|dialysis)\b|\bafter[- ]?care\b|\bfollow[- ]?up\b", re.I)
_SUBORDINATE = re.compile(r"\b(?:when|if|unless|where|because|whose|who|which|for|while|whereas)\b", re.I)
_REFERRAL = re.compile(r"\brefer(?:s|red|ring)?\b(?! hospital| cent)(?:\s+\w+){0,4}?\s+(?:to|out)\b|"
                       r"\breferrals? (?:to|for)\b|\breferred (?:for|elsewhere)\b|\bshifted to\b", re.I)
_SUBGROUP = re.compile(r"\b(?:high[- ]risk|complicated|complex|severe|selected|some|certain|critical|advanced)\b", re.I)
_PARTNER = re.compile(r"\b(?:visiting|in (?:association|collaboration|partnership) with|partner(?:ed)? with|"
                      r"managed by|operated by|run by|franchise|ppp|public[- ]private partnership)\b", re.I)
_CAMP = re.compile(r"\b(?:camps?|outreach|mobile (?:unit|van|clinic)|screening drive)\b", re.I)
_PERSONNEL = re.compile(r"^\s*(?:dr\.?|doctor|prof\.?)\s+[A-Z]|\b(?:dr\.?)\s+[A-Z][a-z]+[^.]{0,80}\b(?:is an?|specialist|"
                        r"consultant|surgeon|expert|experience|trained)\b", re.I)
_RESEARCH = re.compile(r"\b(?:study|studies|trial|cohort|case (?:report|series)|retrospective|prospective|"
                       r"patients (?:were|was)|we (?:report|describe)|was conducted|published|journal|enrolled|"
                       r"listed (?:as|among|on|in)|ranked|top \d+|directory|blog post)\b", re.I)
_EDUCATIONAL = re.compile(r"^\s*(?:what|how|why|when) (?:is|are|does|do)\b|\bis (?:a|an) (?:procedure|test|technique|"
                          r"treatment|type|method|diagnostic)\b|\brefers to\b|\bis used to (?:treat|diagnose|detect|"
                          r"evaluate|remove|open|check|assess)\b|\bsymptoms?\b|\bcauses? of\b|\brisks? of\b|"
                          r"\bbenefits? of\b|\bcosts? (?:of|one|less|about|around)\b|\bthe cost of\b|\bprice of\b|\bcheaper\b|"
                          r"\balternative to\b|\binstead of\b|\bwithout\b|\bprices? of\b|"
                          r"\bavoid(?:s|ing)?\b", re.I)
_COURSE = re.compile(r"\b(?:bachelor|master(?:'?s)?|diploma|degree|course|courses|certificate|b\.?\s?sc|m\.?\s?sc|"
                     r"fellowship|curriculum|syllabus|admissions?|students?|internship|b\.?d\.?t\.?t|m\.?d\.?t|"
                     r"(?:dialysis|perfusion|cardiac care|anaesthesia|anesthesia|radiology|imaging|operation theatre|"
                     r"laboratory|lab) technolog(?:y|ist))\b", re.I)
_TESTIMONIAL = re.compile(r"\b(?:gratitude|thank(?:s|ful)?|testimonial|grateful|impressed|my (?:father|mother|husband|wife|"
                          r"son|daughter)|review(?:ed)? by)\b", re.I)
_PAST = re.compile(r"\b(?:was|were)\b", re.I)
_PRESENT = re.compile(r"\b(?:is|are|provides?|offers?|has|have|available|now)\b", re.I)
_SERVICE_VERB = re.compile(r"\b(?:provides?|offers?|performs?|performed|available|has|have|equipped|facilit\w+|"
                           r"services?|department|unit|done|conducts?|speciali[sz]\w+|treatment|surger\w+|centre|"
                           r"center|lab|wing|ward|includ\w+)\b", re.I)


class ClaimClassifier(Protocol):
    model_id: str
    version: str

    def classify(self, claim: Claim, match: ConceptMatch) -> Classification: ...


def _clause(text: str, start: int, end: int) -> tuple[str, str, str]:
    """(text before the mention, the mention, text after it), cut at clause boundaries."""
    left = text[:start]
    cut = [m.end() for m in _CLAUSE_END.finditer(left)]
    left = left[cut[-1]:] if cut else left
    right = text[end:]
    m = _CLAUSE_END.search(right)
    right = right[:m.start()] if m else right
    return left, text[start:end], right


def _years(text: str) -> tuple[int, ...]:
    return tuple(sorted({int(y) for y in _YEAR.findall(text) if int(y) <= THIS_YEAR}))


class RuleClassifier:
    """Cue phrases, scoped to the clause that mentions the treatment. First matching rule wins."""

    model_id = "rules"

    def __init__(self, version: str = "rules_v2"):
        self.version = version

    def _make(self, claim, match, kind, cues=(), scope="all", temporal="current"):
        if match.role == "ambiguous" and kind in ("OFFERED", "PARTNER_ON_SITE"):
            kind = "AMBIGUOUS"
        years = _years(claim.text)
        if (temporal == "current" and years and max(years) < THIS_YEAR - OLD_YEARS
                and _PAST.search(claim.text) and not _PRESENT.search(claim.text)):
            temporal = "past"                   # "Dialysis was performed for 987 patients in 1998"
            cues = (*cues, f"past tense, {max(years)}")
        return Classification(kind, claim.text, self.model_id, self.version, scope=scope, temporal=temporal,
                              years=years, cues=tuple(cues))

    def classify(self, claim: Claim, match: ConceptMatch) -> Classification:
        t = claim.text
        left, mention, right = _clause(t, match.start, match.end)
        clause = f"{left}{mention}{right}"
        if _ARTIFACT.search(t):
            return self._make(claim, match, "EXTRACTOR_ARTIFACT", ["extractor note"])
        m = _DISCONTINUED.search(clause)
        if m:
            r = _RESTARTED.search(t, m.end())
            if r:
                return self._make(claim, match, "OFFERED", [m.group(0), r.group(0)], temporal="restarted")
            return self._make(claim, match, "DISCONTINUED", [m.group(0)], temporal="past")
        m = _PLANNED.search(clause)
        if m:
            return self._make(claim, match, "PLANNED_OR_COMING_SOON", [m.group(0)], temporal="future")
        m = _OUTSOURCED.search(clause)
        if m and not _SCHEME.search(t):         # an "ESIC tie-up hospital" is empanelled, not outsourcing
            return self._make(claim, match, "OUTSOURCED_OFF_SITE", [m.group(0)])
        mb, ma = _NEG_BEFORE.search(left), _NEG_AFTER.search(right)
        if ma and _SUBORDINATE.search(right[:ma.start(1)]):
            ma = None                           # "... when relatives are not available": another subject
        if mb or ma:
            return self._make(claim, match, "NOT_OFFERED", [(mb or ma).group(0).strip()])
        m = _REFERRAL.search(clause)
        if m:
            scope = "subgroup" if _SUBGROUP.search(left) or _SUBGROUP.search(t[:match.start]) else "all"
            return self._make(claim, match, "REFERRAL_ONLY", [m.group(0)], scope=scope)
        m = _PARTNER.search(clause) or _PARTNER.search(t)
        if m:
            return self._make(claim, match, "PARTNER_ON_SITE", [m.group(0)])
        m = _CAMP.search(clause)
        if m:
            return self._make(claim, match, "OUTREACH_CAMP", [m.group(0)])
        m = _PERSONNEL.search(t)
        if m and not _SERVICE_VERB.search(left):
            return self._make(claim, match, "PERSONNEL_ONLY", [m.group(0).strip()])
        m = _RESEARCH.search(t)
        if m:
            return self._make(claim, match, "RESEARCH_OR_LISTING", [m.group(0)])
        m = _AFTERCARE.search(t)
        if m:
            return self._make(claim, match, "AMBIGUOUS", ["aftercare: " + m.group(0)])
        m = _COURSE.search(t)
        if m:
            return self._make(claim, match, "EDUCATIONAL_OR_GENERIC", ["training course: " + m.group(0)])
        m = _EDUCATIONAL.search(t)
        if m:
            return self._make(claim, match, "EDUCATIONAL_OR_GENERIC", [m.group(0)])
        m = _TESTIMONIAL.search(t)
        if m:
            return self._make(claim, match, "AMBIGUOUS", ["testimonial: " + m.group(0)])
        if claim.field in ("capability", "procedure", "equipment", "specialties"):
            return self._make(claim, match, "OFFERED", ["list item"])
        if claim.context == "blog":
            return self._make(claim, match, "EDUCATIONAL_OR_GENERIC", ["blog page"])
        if claim.context == "navigation" or len(t.split()) <= 8 or _SERVICE_VERB.search(t):
            return self._make(claim, match, "OFFERED", ["service statement"])
        return self._make(claim, match, "AMBIGUOUS", ["no service cue"])


# --------------------------------------------------------------------------- LLM
SYSTEM_PROMPT = """You label one claim about a health facility in India. The claim mentions a treatment.
Decide what the claim says about THIS facility providing that treatment. Answer with JSON only.

assertion_type, one of:
OFFERED: the facility provides it now. PARTNER_ON_SITE: a partner-run unit or visiting specialist provides
it on the premises. OUTSOURCED_OFF_SITE: arranged through another provider elsewhere. REFERRAL_ONLY:
patients are sent elsewhere. PLANNED_OR_COMING_SOON: not yet available. DISCONTINUED: stopped.
NOT_OFFERED: explicitly unavailable. EDUCATIONAL_OR_GENERIC: describes the treatment, not a service.
OUTREACH_CAMP: done at camps. PERSONNEL_ONLY: a doctor's expertise, not the facility's service.
RESEARCH_OR_LISTING: a study, ranking or directory listing. EXTRACTOR_ARTIFACT: a note that information
was missing from some content. AMBIGUOUS: cannot tell.

quote: the shortest exact span of the claim that justifies the label, copied character for character.
scope: "subgroup" if only some patients are affected (e.g. high-risk cases), else "all".
temporal: "current", "past", "future" or "restarted".
entity_mentions: every organisation named in the claim, copied exactly. Do not judge who they are."""

LLM_SCHEMA = {
    "type": "object",
    "properties": {
        "assertion_type": {"type": "string", "enum": [a for a in ASSERTION_TYPES if a != "OTHER_ENTITY"]},
        "quote": {"type": "string"},
        "scope": {"type": "string", "enum": ["all", "subgroup"]},
        "temporal": {"type": "string", "enum": ["current", "past", "future", "restarted"]},
        "entity_mentions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["assertion_type", "quote", "scope", "temporal", "entity_mentions"],
    "additionalProperties": False,
}


class QuoteRejected(ValueError):
    pass


def parse_llm_output(raw: str, claim_text: str, model_id: str, version: str) -> Classification:
    """Validate the model's JSON. The quote and every entity mention must be exact substrings."""
    data = json.loads(raw)
    kind = data.get("assertion_type")
    if kind not in ASSERTION_TYPES or kind == "OTHER_ENTITY":
        raise ValueError(f"unknown assertion_type {kind!r}")
    quote = data.get("quote") or ""
    if not quote or quote not in claim_text:
        raise QuoteRejected(f"quote is not an exact substring of the claim: {quote!r}")
    mentions = tuple(m for m in data.get("entity_mentions") or [] if m and m in claim_text)
    return Classification(kind, quote, model_id, version, scope=data.get("scope", "all"),
                          temporal=data.get("temporal", "current"), years=_years(claim_text),
                          entity_mentions=mentions)


class LLMClassifier:
    """A Databricks Foundation Model API endpoint, through the same client as src/hackathon/rag.py."""

    def __init__(self, endpoint: str, version: str = "llm_v1", client=None, temperature: float = 0.0,
                 max_tokens: int = 600, concept_names: dict | None = None):
        self.model_id, self.version = endpoint, version
        self._client, self.temperature, self.max_tokens = client, temperature, max_tokens
        self.concept_names = concept_names or {}

    @property
    def client(self):
        if self._client is None:
            from .. import rag  # reuse the workspace client and auth the RAG app already uses
            self._client = rag._workspace().serving_endpoints.get_open_ai_client()
        return self._client

    def classify(self, claim: Claim, match: ConceptMatch) -> Classification:
        name = self.concept_names.get(match.concept_id, match.concept_id)
        user = (f"Treatment: {name} (mentioned as: {match.matched_text!r})\n"
                f"Field: {claim.field}\nClaim: {claim.text}")
        resp = self.client.chat.completions.create(
            model=self.model_id, temperature=self.temperature, max_tokens=self.max_tokens,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}],
            response_format={"type": "json_schema", "json_schema": {"name": "claim_label", "schema": LLM_SCHEMA}},
        )
        from ..rag import _text_from_content
        raw = _text_from_content(resp.choices[0].message.content)
        return parse_llm_output(raw, claim.text, self.model_id, self.version)


class DualPass:
    """Rule pass plus model pass. The model's label wins unless it fails validation; disagreements are flagged."""

    def __init__(self, rules: RuleClassifier, model: ClaimClassifier):
        self.rules, self.model = rules, model
        self.model_id, self.version = model.model_id, f"{model.version}+{rules.version}"

    def classify(self, claim: Claim, match: ConceptMatch) -> Classification:
        r = self.rules.classify(claim, match)
        try:
            m = self.model.classify(claim, match)
        except Exception as exc:                # rejected quote, bad JSON, endpoint error: keep the rule label
            log.warning("model label rejected for %s: %s", claim.claim_id, exc)
            return replace(r, disagreement=True, rule_assertion_type=r.assertion_type)
        return replace(m, disagreement=m.assertion_type != r.assertion_type, rule_assertion_type=r.assertion_type,
                       cues=r.cues)


class CachedClassifier:
    """Caches labels by (claim content hash, concept, match kind, model, version) in SQLite.

    Labels never depend on which facility carries the claim (entity matching runs later, in code), so
    one label serves every copy of the same text, and re-runs only pay for new claims or new models.
    """

    def __init__(self, inner: ClaimClassifier, path: str | Path):
        self.inner, self.model_id, self.version = inner, inner.model_id, inner.version
        self.db = sqlite3.connect(str(path))
        self.db.execute("CREATE TABLE IF NOT EXISTS labels (key TEXT PRIMARY KEY, value TEXT)")
        self.hits = self.misses = 0

    def _key(self, claim: Claim, match: ConceptMatch) -> str:
        return "|".join([claim.content_hash, claim.field, claim.context or "", match.concept_id, match.role,
                         match.matched_text.lower(), self.model_id, self.version])

    def classify(self, claim: Claim, match: ConceptMatch) -> Classification:
        key = self._key(claim, match)
        row = self.db.execute("SELECT value FROM labels WHERE key = ?", (key,)).fetchone()
        if row:
            self.hits += 1
            d = json.loads(row[0])
            d["years"], d["entity_mentions"], d["cues"] = tuple(d["years"]), tuple(d["entity_mentions"]), tuple(d["cues"])
            if d["quote"] not in claim.text:    # same normalized text, different casing or spacing
                d["quote"] = claim.text
            return Classification(**d)
        self.misses += 1
        c = self.inner.classify(claim, match)
        self.db.execute("INSERT OR REPLACE INTO labels VALUES (?, ?)", (key, json.dumps(asdict(c))))
        return c

    def commit(self):
        self.db.commit()
