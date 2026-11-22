"""End-to-end: facilities -> claim ledger -> concept tags -> labels -> evidence -> one record per pair.

Every facility is split, tagged and labeled once; a hospital-treatment verdict is then an aggregation
over the claims tagged with that treatment. Adding a treatment means adding a KB entry and re-tagging;
labels are cached by claim text, so nothing already labeled is paid for twice.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

from .. import cleaning
from ..config import Config
from . import copies, ledger, sources
from .classify import CachedClassifier, ClaimClassifier, DualPass, LLMClassifier, RuleClassifier
from .decide import POSITIVE, decide
from .entities import FacilityProfile, check_entity
from .kb import KBEntry, PatternTagger, kb_version, load_kb
from .records import Claim, ConceptMatch, Evidence
from .scoring import HeuristicScorer, Scorer, is_stale

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = REPO_ROOT / "configs" / "verification.yaml"
MAX_LISTED = 8          # evidence items written per list; counts cover the rest


def load_config(path: str | Path = DEFAULT_CONFIG) -> tuple[dict, str]:
    text = Path(path).read_text(encoding="utf-8")
    return yaml.safe_load(text), hashlib.sha1(text.encode()).hexdigest()[:10]


def read_facilities(path: str | Path) -> pd.DataFrame:
    """A CSV export of silver_facilities_verified (tools/verify_locations.py writes the same columns)."""
    df = pd.read_csv(path, dtype=str, keep_default_na=False, low_memory=False)
    return df.replace({"": None})


def make_classifier(cfg: dict, use_llm: bool, cache_path: str | Path | None, entries: dict[str, KBEntry]):
    rules = RuleClassifier(cfg["models"]["rule_classifier_version"])
    clf: ClaimClassifier = rules
    if use_llm:
        endpoint = cfg["models"]["llm_endpoint"] or Config().llm_endpoint
        llm = LLMClassifier(endpoint, cfg["models"]["llm_classifier_version"],
                            temperature=cfg["models"]["llm_temperature"], max_tokens=cfg["models"]["llm_max_tokens"],
                            concept_names={k: e.canonical_name for k, e in entries.items()})
        clf = DualPass(rules, llm)
    return CachedClassifier(clf, cache_path) if cache_path else clf


class Verifier:
    def __init__(self, config_path=DEFAULT_CONFIG, concepts=None, classifier: ClaimClassifier | None = None,
                 scorer: Scorer | None = None, use_llm: bool = False, cache_path=None):
        self.cfg, self.config_hash = load_config(config_path)
        self.entries = load_kb(concepts)
        self.kb_version = kb_version(self.entries)
        self.tagger = PatternTagger(self.entries)
        self.classifier = classifier or make_classifier(self.cfg, use_llm, cache_path, self.entries)
        self.rules = RuleClassifier(self.cfg["models"]["rule_classifier_version"])
        self.scorer = scorer or HeuristicScorer(self.cfg)

    # ----------------------------------------------------------------- stage 1-4: the ledger, once
    def build(self, facilities: pd.DataFrame, crawl_db=None):
        t0 = time.time()
        self.facilities = facilities.set_index("unique_id", drop=False)
        self.profiles = {r["unique_id"]: FacilityProfile.build(r["unique_id"], r.get("name"), r.get("address_city"))
                         for r in facilities.to_dict("records")}
        claims = ledger.build_ledger(facilities)
        if crawl_db:
            claims += ledger.official_claims(crawl_db, facilities, self.cfg["reliability"]["chain_domain_min_facilities"])
        self.shared = ledger.shared_content_sizes(facilities)
        tags: dict[str, list[ConceptMatch]] = {}
        for c in claims:
            ms = self.tagger.tag(c.text, c.field)
            if ms:
                tags[c.claim_id] = ms
        tagged = [c for c in claims if c.claim_id in tags]
        cc = self.cfg["copies"]
        copied = copies.block_copies(claims, self.profiles, cc["block_min_shared_items"], cc["block_min_share"],
                                     cc["min_facilities"], cc["block_item_max_df"])
        copied.update(copies.text_copies(tagged, self.profiles, cc["min_words"], cc["min_facilities"],
                                         cc["minhash_perms"], cc["minhash_bands"], cc["near_duplicate_jaccard"]))
        self.claims_by_fac = defaultdict(list)
        for c in tagged:
            self.claims_by_fac[c.unique_id].append(c)
        self.tags, self.copied = tags, copied
        log.info("build: claims=%d tagged=%d copied=%d seconds=%.1f", len(claims), len(tagged),
                 sum(1 for c in tagged if c.claim_id in copied), time.time() - t0)
        return self

    # ----------------------------------------------------------------- stage 5: labels -> evidence
    def _evidence(self, claim: Claim, m: ConceptMatch) -> Evidence:
        clf = self.rules if m.role == "confusable" else self.classifier
        lab = clf.classify(claim, m)
        prof = self.profiles[claim.unique_id]
        ent = check_entity(claim.text, prof)
        mentions = list(lab.entity_mentions) or list(ent.mentions)
        if lab.entity_mentions:                 # the model's verbatim mentions, matched in code
            match = "this" if any(prof.matches(x) for x in mentions) else ent.entity_match
        else:
            match = ent.entity_match
        if claim.source_type == "official_site_page" and match == "unnamed":
            match = "this"                      # the facility's own site is about the facility
        elif claim.source_type == "official_site_unmatched" and match != "this":
            match = "other"                     # a website that never names the facility is about someone else
        elif claim.source_type == "official_chain_page" and match in ("unnamed", "this"):
            match = "chain"
        kind = lab.assertion_type
        if match == "other" and kind in POSITIVE + ("AMBIGUOUS",):
            kind = "OTHER_ENTITY"
        info = self.copied.get(claim.claim_id)
        ev = Evidence(
            claim_id=claim.claim_id, quote=lab.quote if lab.quote in claim.text else claim.text, field=claim.field,
            assertion_type=kind, concept_ids=[m.concept_id], role=m.role, source_url=claim.source_url,
            source_type=claim.source_type, lineage=claim.lineage, origin_cluster_id=claim.origin_cluster_id,
            source_date=claim.source_date, retrieval_date=claim.retrieval_date, entity_match=match,
            relevance_score=round(m.specificity, 3), classifier_model=lab.classifier_model,
            classifier_version=lab.classifier_version, sub_id=m.sub_id, specificity=m.specificity, key=m.key,
            negates_parent=m.negates_parent, scope=lab.scope, temporal=lab.temporal, copied=info is not None,
            copy_kind=info.kind if info else None, copy_cluster_size=info.facilities if info else 0,
            entity_mentions=mentions, disagreement=lab.disagreement, rule_assertion_type=lab.rule_assertion_type,
            cues=list(lab.cues))
        ev.stale = is_stale(ev, self.cfg["scoring"]["stale_after_years"])
        return ev

    def evidence_for(self, uid: str, concept_id: str) -> list[Evidence]:
        out, seen = [], set()
        for c in self.claims_by_fac.get(uid, []):
            for m in self.tags[c.claim_id]:
                if m.concept_id != concept_id:
                    continue
                # one piece of evidence per origin, text and role: site menus repeat on every page,
                # and one claim can match several children of the same concept
                key = (c.origin_cluster_id, c.content_hash, m.role, m.sub_id if m.role == "prerequisite" else "")
                if key in seen:
                    continue
                seen.add(key)
                out.append(self._evidence(c, m))
        return out

    # ----------------------------------------------------------------- stage 6: one record
    def verify(self, uid: str, concept_id: str, requested: str | None = None) -> dict:
        entry = self.entries[concept_id]
        row = self.facilities.loc[uid].to_dict()
        items = self.evidence_for(uid, concept_id)
        if any(e.entity_match == "other" and e.lineage == "vf_extraction" for e in items):
            # The record carries another facility's claims about this treatment, so its unnamed claims
            # may have come from the same page: they can no longer be read as this facility's.
            for e in items:
                if e.lineage == "vf_extraction" and e.entity_match == "unnamed":
                    e.entity_match = "ambiguous"
        score = self.scorer.score(items, entry, row.get("facilityTypeId"))
        identity = sources.identity_checks(row, self.shared.get(uid, 1))
        d = decide(items, score, entry, row.get("facilityTypeId"), identity, self.cfg)
        used = {id(e) for e in d.pop("used")}
        neg_types = self.cfg["scoring"]["negative_types"]

        def pick(pred, key=lambda e: -abs(e.weight)):
            sel = sorted([e for e in items if pred(e)], key=key)
            return [e.to_dict() for e in sel[:MAX_LISTED]], len(sel)

        counted = ("this", "unnamed", "chain")

        def is_direct(e):
            return (e.role in ("direct", "ambiguous") and e.assertion_type in POSITIVE and e.entity_match in counted
                    and not e.copied and not e.stale)

        direct, n_direct = pick(is_direct)
        support, n_support = pick(lambda e: e.role == "prerequisite" and e.sub_id in score.prerequisites_present)
        contra, n_contra = pick(lambda e: e.role == "direct" and e.assertion_type in neg_types)
        rest, n_rest = pick(lambda e: e.role != "prerequisite" and not is_direct(e)
                            and not (e.role == "direct" and e.assertion_type in neg_types))
        return {
            "hospital_id": uid,
            "hospital_name": row.get("name"),
            "hospital_location": {
                "city": row.get("address_city"), "state": row.get("address_stateOrRegion"),
                "pin": row.get("address_zipOrPostcode"),
                "resolved_latitude": _num(row.get("resolved_latitude")),
                "resolved_longitude": _num(row.get("resolved_longitude")),
                "location_status": row.get("location_status"), "location_confidence": row.get("location_confidence")},
            "facility_type": row.get("facilityTypeId"),
            "target_treatment": requested or entry.canonical_name,
            "normalized_treatment": {"concept_id": entry.concept_id, "name": entry.canonical_name,
                                     "kb_version": f"{entry.kb_version}#{entry.raw_hash}",
                                     "kb_review_status": entry.review_status},
            "verification_status": d["verification_status"],
            "reason_codes": d["reason_codes"],
            "confidence_score": d["confidence_score"],
            "confidence_basis": self.scorer.basis,
            "p_provides": score.p_provides,
            "independent_origin_count": d["independent_origin_count"],
            "direct_evidence": direct,
            "supporting_evidence": support,
            "contradictory_evidence": contra,
            "not_counted_evidence": rest,
            "evidence_counts": {"direct": n_direct, "supporting": n_support, "contradictory": n_contra,
                                "not_counted": n_rest, "used_in_decision": len(used)},
            "missing_prerequisites": d["missing_prerequisites"],
            "prerequisites_present": d["prerequisites_present"],
            "reasoning_summary": d["reasoning_summary"],
            "requires_manual_review": d["requires_manual_review"],
            "review_reasons": d["review_reasons"],
            "identity_checks": identity,
            "record_sources": sources.record_sources(row),
            "score_terms": {"prior": score.prior, "logit": score.logit, "terms": score.terms},
            "pipeline_version": self.cfg["pipeline_version"],
            "config_hash": self.config_hash,
            "classifier": {"model": self.classifier.model_id, "version": self.classifier.version},
            "run_timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    def run(self, concepts=None):
        """Yield one record per facility and concept."""
        for cid in concepts or list(self.entries):
            for uid in self.facilities.index:
                yield self.verify(uid, cid)

    def normalize_treatment(self, text: str) -> str:
        """Map a requested treatment to a KB concept using the KB's own patterns."""
        q = text.strip()
        for cid, e in self.entries.items():
            if q.lower() in (cid.replace("_", " "), e.canonical_name.lower()):
                return cid
        hits = {m.concept_id for m in self.tagger.tag(q, "procedure") if m.role == "direct"}
        if len(hits) == 1:
            return hits.pop()
        confus = {m.concept_id for m in self.tagger.tag(q, "procedure") if m.role in ("confusable", "ambiguous")}
        raise ValueError(f"cannot map {text!r} to one KB treatment (direct: {sorted(hits)}, "
                         f"confusable or ambiguous for: {sorted(confus)}); known: {sorted(self.entries)}")


def _num(v):
    if cleaning.missing(v) or v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def write_jsonl(records, path: str | Path) -> int:
    n = 0
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += 1
    return n
