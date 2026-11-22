"""recommend(): the red-flag screen first, then every later stage isolated.

Order: screen the raw message -> build the emergency block -> extraction -> urgency (raise only) ->
care needs -> location check -> data -> ranking. Each stage after the screen runs in its own try:
a failure adds a degraded reason and the response still carries the emergency block. If the screen
itself fails, the response escalates to emergency (when unsure, escalate).

Privacy: nothing here logs, stores or returns the message or the patient's coordinates.
"""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Callable, Optional, Protocol

import yaml

from .contract import (
    CareNeed,
    ContactNumber,
    EmergencyBlock,
    Recommendation,
    RecommendRequest,
    RecommendResponse,
    SearchInfo,
    SymptomAnalysis,
    Triage,
    Versions,
)
from .red_flags import REPO_ROOT, RedFlagRules, Screen, higher

log = logging.getLogger("hackathon.navigation")
DEFAULT_CONFIG = REPO_ROOT / "configs" / "navigation.yaml"


def load_nav_config(path: str | Path = DEFAULT_CONFIG) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------- interfaces of later stages
class Extractor(Protocol):
    def extract(self, message: str) -> SymptomAnalysis: ...


class CareNeedMapper(Protocol):
    version: str

    def urgency(self, analysis: Optional[SymptomAnalysis], screen: Optional[Screen]) -> str: ...

    def care_needs(self, analysis: Optional[SymptomAnalysis], screen: Optional[Screen], urgency: str) -> list[CareNeed]: ...

    def follow_up_questions(self, analysis: Optional[SymptomAnalysis]) -> list[str]: ...


class Ranker(Protocol):
    def rank(self, store, needs: list[CareNeed], urgency: str, lat: float, lon: float,
             max_results: int) -> tuple[list[Recommendation], SearchInfo, Optional[str], dict]: ...


# ---------------------------------------------------------------------- emergency block
def _contact(c: dict) -> ContactNumber:
    return ContactNumber(**{k: c[k] for k in ("label", "number", "alt_number", "note", "source_url") if k in c})


def emergency_block(screen: Optional[Screen], cfg: dict, generic: bool = False) -> Optional[EmergencyBlock]:
    """The block the client must render first, built from config only."""
    msgs, contacts = cfg["messages"], cfg["contacts"]
    if screen is None or generic:
        return EmergencyBlock(call_emergency_services=True, message=msgs["screen_failed" if screen is None else "emergency_generic"],
                              numbers=[_contact(contacts["emergency"]), _contact(contacts["ambulance"])],
                              transport_advice=msgs["transport_advice"])
    if not screen.emergency:
        return None
    cats = list(dict.fromkeys(f.category for f in screen.fired if f.floor == "emergency"))
    numbers = [_contact(contacts["emergency"]), _contact(contacts["ambulance"])]
    if screen.crisis:
        numbers.append(_contact(contacts["tele_manas"]))
    return EmergencyBlock(call_emergency_services=True, crisis=screen.crisis,
                          message=msgs["crisis"] if screen.crisis else msgs["emergency_generic"],
                          category_messages=[msgs["category"][c] for c in cats if c in msgs["category"]],
                          numbers=numbers, transport_advice=msgs["transport_advice"])


def in_coverage(lat: float, lon: float, bbox: dict) -> bool:
    if lat == 0 and lon == 0:
        return False
    return bbox["lat_min"] <= lat <= bbox["lat_max"] and bbox["lon_min"] <= lon <= bbox["lon_max"]


# ---------------------------------------------------------------------- the service
class Navigator:
    def __init__(self, rules: RedFlagRules, cfg: dict, extractor: Optional[Extractor] = None,
                 mapper: Optional[CareNeedMapper] = None, store_provider: Optional[Callable[[], object]] = None,
                 ranker: Optional[Ranker] = None):
        self.rules, self.cfg = rules, cfg
        self.extractor, self.mapper, self.store_provider, self.ranker = extractor, mapper, store_provider, ranker

    def recommend(self, req: RecommendRequest) -> RecommendResponse:
        t0 = time.monotonic()
        rid = uuid.uuid4().hex
        msgs = self.cfg["messages"]
        reasons: list[str] = []

        # 1. red-flag screen on the raw message
        try:
            screen: Optional[Screen] = self.rules.screen(req.patient_message)
            floor = screen.floor
            emergency = emergency_block(screen, self.cfg)
        except Exception:                       # a bug here must not hide an emergency
            log.exception("red-flag screen failed (request %s)", rid)
            screen, floor, emergency = None, "emergency", emergency_block(None, self.cfg)
            reasons.append("red_flag_screen_failed")

        resp = RecommendResponse(
            request_id=rid, disclaimer=self.cfg["disclaimer"], emergency=emergency,
            triage=Triage(urgency=floor or "unclear", rule_floor=floor, reason="", advice=""),
            versions=Versions(navigation=str(self.cfg.get("navigation_version")), red_flag_rules=self.rules.version))
        if screen is not None:
            resp.triage.red_flag_rule_ids = screen.rule_ids
            resp.triage.red_flag_categories = screen.categories

        try:
            self._rest(req, screen, floor, resp, reasons)
        except Exception:                       # last line of defence: keep what we have
            log.exception("navigation failed after the screen (request %s)", rid)
            reasons.append("internal_error")

        u = resp.triage.urgency
        if u == "emergency" and resp.emergency is None:      # raised by extraction, or degraded
            resp.emergency = emergency_block(screen, self.cfg, generic=True)
        resp.triage.reason = msgs["triage_reason"][u]
        resp.triage.advice = msgs["triage_advice"][u]
        resp.degraded = bool(reasons)
        resp.degraded_reasons = reasons
        log.info("request=%s urgency=%s rules=%s needs=%d results=%d degraded=%s latency_ms=%d", rid, u,
                 ",".join(resp.triage.red_flag_rule_ids) or "-", len(resp.care_needs), len(resp.recommendations),
                 ",".join(reasons) or "-", int((time.monotonic() - t0) * 1000))
        return resp

    def _rest(self, req: RecommendRequest, screen: Optional[Screen], floor: Optional[str],
              resp: RecommendResponse, reasons: list[str]) -> None:
        # 2. extraction (the extractor falls back to rules itself; reaching except means both failed)
        analysis = None
        if self.extractor is not None:
            try:
                analysis = self.extractor.extract(req.patient_message)
                resp.symptom_analysis = analysis
            except Exception:
                log.exception("extraction failed (request %s)", resp.request_id)
                reasons.append("extraction_failed")
        else:
            reasons.append("extraction_unavailable")

        # 3. urgency: extraction may raise it, never lower it below the rule floor
        level = None
        if self.mapper is not None:
            try:
                level = self.mapper.urgency(analysis, screen)
            except Exception:
                log.exception("urgency mapping failed (request %s)", resp.request_id)
                reasons.append("urgency_mapping_failed")
        urgency = higher(floor, level) if level in ("routine", "urgent", "emergency") else (floor or "unclear")
        if screen is None:
            urgency = "emergency"
        resp.triage.urgency = urgency
        resp.triage.raised_by_extraction = floor != urgency and urgency != "unclear"

        # 4. care needs
        needs: list[CareNeed] = []
        if self.mapper is not None:
            try:
                needs = self.mapper.care_needs(analysis, screen, urgency)
            except Exception:
                log.exception("care-need mapping failed (request %s)", resp.request_id)
                reasons.append("care_need_mapping_failed")
        resp.care_needs = needs
        if urgency == "unclear" or (not needs and urgency != "emergency"):
            resp.triage.urgency = "unclear" if urgency in ("unclear", "routine") else urgency
            if self.mapper is not None:
                try:
                    resp.follow_up_questions = self.mapper.follow_up_questions(analysis)
                except Exception:
                    reasons.append("follow_up_failed")
            if resp.triage.urgency == "unclear":
                return

        # 5. location
        if not in_coverage(req.latitude, req.longitude, self.cfg["coverage"]["bbox"]):
            resp.coverage_note = ("Your location is outside the area our facility data covers (India), so we "
                                  "can't suggest facilities. If this may be an emergency, call 112.")
            return

        # 6. data
        if self.store_provider is None or self.ranker is None:
            reasons.append("data_unavailable")
            return
        try:
            store = self.store_provider()
        except Exception:
            log.exception("facility data unavailable (request %s)", resp.request_id)
            reasons.append("data_unavailable")
            return

        # 7. ranking
        try:
            recs, search, note, versions = self.ranker.rank(store, needs, resp.triage.urgency, req.latitude,
                                                            req.longitude, req.max_results)
        except Exception:
            log.exception("ranking failed (request %s)", resp.request_id)
            reasons.append("ranking_failed")
            return
        resp.recommendations, resp.search, resp.coverage_note = recs, search, note
        resp.versions.kb = versions.get("kb")
        resp.versions.verification_run = versions.get("verification_run")
        if self.mapper is not None:
            resp.versions.care_needs = self.mapper.version

    # ------------------------------------------------------------------ health
    def health(self) -> dict:
        data = {"loaded": False, "error": None}
        if self.store_provider is not None:
            try:
                store = self.store_provider()
                data = {"loaded": True, "error": None, **getattr(store, "summary", lambda: {})()}
            except Exception as exc:
                data = {"loaded": False, "error": type(exc).__name__}
        llm = getattr(self.extractor, "status", lambda: {"configured": False})() if self.extractor else {"configured": False}
        return {"status": "ok" if data["loaded"] else "degraded",
                "red_flag_rules": {"version": self.rules.version, "review_status": self.rules.review_status},
                "data": data, "llm": llm}


def build_navigator(config_path: str | Path = DEFAULT_CONFIG, data_paths: Optional[dict] = None,
                    use_llm: Optional[bool] = None) -> Navigator:
    """The production wiring. A bad red_flags.yaml raises here: the service must not start without it."""
    from .care_needs import CareNeedTable
    from .extract import make_extractor
    from .rank import NavigationRanker
    from .store import lazy_file_store

    cfg = load_nav_config(config_path)
    rules = RedFlagRules.load()
    mapper = CareNeedTable.load()
    extractor = make_extractor(cfg, mapper.terms, rules, use_llm=use_llm)
    return Navigator(rules, cfg, extractor=extractor, mapper=mapper,
                     store_provider=lazy_file_store(data_paths), ranker=NavigationRanker(cfg, mapper))
