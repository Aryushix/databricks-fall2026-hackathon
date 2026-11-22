"""End-to-end: facilities -> claim ledger -> concept tags -> labels -> evidence -> one record per pair.

Every facility is split, tagged and labeled once; a hospital-treatment verdict is then an aggregation
over the claims tagged with that treatment. Adding a treatment means adding a KB entry and re-tagging;
labels are cached by claim text, so nothing already labeled is paid for twice.
"""

from __future__ import annotations

import hashlib
import json
import logging
import multiprocessing
import shutil
import tempfile
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
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
from .runlog import event
from .scoring import HeuristicScorer, Scorer, is_stale

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = REPO_ROOT / "configs" / "verification.yaml"
MAX_LISTED = 8          # evidence items written per list; counts cover the rest
MIN_PARALLEL_CLAIMS = 50_000   # below this, starting worker processes costs more than it saves
SHARDS_PER_WORKER = 4          # several shards per process keep the workers evenly loaded


# ---------------------------------------------------------------------- worker processes
# Workers are started with "spawn" (the macOS default, and safe with threads), so everything they
# need arrives through these module-level functions. Each worker rebuilds the tagger or a verifier from
# the same KB files and config; the parent checks the KB version matches.
_WORKER: dict = {}


def _tag_init(concepts, expected_kb_version):
    entries = load_kb(concepts)
    if kb_version(entries) != expected_kb_version:
        raise RuntimeError("KB files changed while the run was starting")
    _WORKER["tagger"] = PatternTagger(entries)


def _tag_chunk(items):
    tagger = _WORKER["tagger"]
    return [(cid, ms) for cid, text, field in items if (ms := tagger.tag(text, field))]


def _verify_shard(task):
    """Verify one shard of facilities for every concept, writing one part file per concept."""
    config_path, concepts, state, part_dir, shard_no = task
    v = Verifier(config_path, concepts, use_llm=False)
    v.facilities, v.profiles, v.claims_by_fac = state["facilities"], state["profiles"], state["claims_by_fac"]
    v.tags, v.copied, v.shared = state["tags"], state["copied"], state["shared"]
    for cid in concepts:
        with open(Path(part_dir) / f"{cid}.{shard_no:05d}.jsonl", "w", encoding="utf-8") as fh:
            for uid in v.facilities.index:
                fh.write(json.dumps(v.verify(uid, cid), ensure_ascii=False) + "\n")
    return shard_no


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
                 scorer: Scorer | None = None, use_llm: bool = False, cache_path=None, workers: int = 1,
                 min_parallel_claims: int = MIN_PARALLEL_CLAIMS):
        self.config_path = str(config_path)
        self.workers, self.min_parallel_claims = max(1, int(workers)), min_parallel_claims
        self.use_llm = use_llm
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
        event(log, "ledger", facilities=len(facilities), claims=len(claims), seconds=round(time.time() - t0, 1))
        if crawl_db:
            t1 = time.time()
            official = ledger.official_claims(crawl_db, facilities, self.cfg["reliability"]["chain_domain_min_facilities"])
            claims += official
            event(log, "official_pages", claims=len(official), seconds=round(time.time() - t1, 1))
        self.shared = ledger.shared_content_sizes(facilities)
        t1 = time.time()
        tags = self._tag_all(claims)
        tagged = [c for c in claims if c.claim_id in tags]
        event(log, "tagging", claims=len(claims), tagged=len(tagged), concepts=len(self.entries),
              workers=self._tag_workers(len(claims)), seconds=round(time.time() - t1, 1))
        cc = self.cfg["copies"]
        copied = copies.block_copies(claims, self.profiles, cc["block_min_shared_items"], cc["block_min_share"],
                                     cc["min_facilities"], cc["block_item_max_df"])
        copied.update(copies.text_copies(tagged, self.profiles, cc["min_words"], cc["min_facilities"],
                                         cc["minhash_perms"], cc["minhash_bands"], cc["near_duplicate_jaccard"]))
        self.claims_by_fac = defaultdict(list)
        for c in tagged:
            self.claims_by_fac[c.unique_id].append(c)
        self.tags, self.copied = tags, copied
        event(log, "build", claims=len(claims), tagged=len(tagged),
              copied=sum(1 for c in tagged if c.claim_id in copied), seconds=round(time.time() - t0, 1))
        return self

    # ----------------------------------------------------------------- parallel helpers
    def _tag_workers(self, n_claims: int) -> int:
        """Parallel tagging only for the default pattern tagger (a swapped-in tagger runs in-process)."""
        if self.workers <= 1 or n_claims < self.min_parallel_claims or type(self.tagger) is not PatternTagger:
            return 1
        return self.workers

    def _tag_all(self, claims: list[Claim]) -> dict[str, list[ConceptMatch]]:
        workers = self._tag_workers(len(claims))
        if workers == 1:
            return {c.claim_id: ms for c in claims if (ms := self.tagger.tag(c.text, c.field))}
        items = [(c.claim_id, c.text, c.field) for c in claims]
        size = max(1000, len(items) // (workers * SHARDS_PER_WORKER) + 1)
        chunks = [items[i:i + size] for i in range(0, len(items), size)]
        tags: dict[str, list[ConceptMatch]] = {}
        with ProcessPoolExecutor(workers, mp_context=multiprocessing.get_context("spawn"), initializer=_tag_init,
                                 initargs=(list(self.entries), self.kb_version)) as ex:
            for part in ex.map(_tag_chunk, chunks):
                tags.update(part)
        return tags

    def _shard_state(self, uids) -> dict:
        claims = {u: self.claims_by_fac.get(u, []) for u in uids}
        ids = {c.claim_id for cs in claims.values() for c in cs}
        return {"facilities": self.facilities.loc[list(uids)], "profiles": {u: self.profiles[u] for u in uids},
                "claims_by_fac": claims, "tags": {k: self.tags[k] for k in ids},
                "copied": {k: self.copied[k] for k in ids if k in self.copied},
                "shared": {u: self.shared[u] for u in uids if u in self.shared}}

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
        """Yield one record per facility and concept: concepts in order, facilities in input order.

        With workers > 1 (rules pass only), facility shards are verified in worker processes and the
        records come back in exactly the same order as a single-process run."""
        concepts = list(concepts or self.entries)
        # rules-only (the label cache only matters for paid model calls); a model pass stays in one
        # process, since it is rate-limited by the endpoint and shares one SQLite label cache
        rules_only = type(getattr(self.classifier, "inner", self.classifier)) is RuleClassifier
        parallel = self.workers > 1 and not self.use_llm and rules_only and len(self.facilities) >= 2 * self.workers
        if not parallel:
            for cid in concepts:
                t0 = time.time()
                for uid in self.facilities.index:
                    yield self.verify(uid, cid)
                event(log, "verify", concept=cid, records=len(self.facilities), workers=1, seconds=round(time.time() - t0, 1))
            return
        yield from self._run_parallel(concepts)

    def _run_parallel(self, concepts):
        uids = list(self.facilities.index)
        n = min(len(uids), self.workers * SHARDS_PER_WORKER)
        size = -(-len(uids) // n)
        shards = [uids[i:i + size] for i in range(0, len(uids), size)]
        part_dir = Path(tempfile.mkdtemp(prefix="verify-parts-"))
        try:
            t0 = time.time()
            tasks = [(self.config_path, concepts, self._shard_state(s), str(part_dir), k) for k, s in enumerate(shards)]
            with ProcessPoolExecutor(self.workers, mp_context=multiprocessing.get_context("spawn")) as ex:
                list(ex.map(_verify_shard, tasks))
            event(log, "verify", concepts=len(concepts), records=len(uids) * len(concepts), workers=self.workers,
                  shards=len(shards), seconds=round(time.time() - t0, 1))
            for cid in concepts:
                for k in range(len(shards)):
                    with open(part_dir / f"{cid}.{k:05d}.jsonl", encoding="utf-8") as fh:
                        for line in fh:
                            yield json.loads(line)
        finally:
            shutil.rmtree(part_dir, ignore_errors=True)

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
