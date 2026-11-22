"""Zero-shot clinical query expansion using Databricks Foundation Model with local caching and offline fallback."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from ..config import Config

log = logging.getLogger("hackathon.navigation.query_expansion")

REPO_ROOT = Path(__file__).resolve().parents[3]
CACHE_FILE = REPO_ROOT / "configs" / "services_cache.json"

SYSTEM_PROMPT = """You are a clinical director and medical ontologist.
Your role is to translate any user healthcare service request (which may be lay terms, slang, regional phrasing, or colloquial English) into standardized clinical requirements across 4 pillars:
1. procedures: clinical/surgical procedures, abbreviations (e.g. CABG, PCNL, hemodialysis), and medical terms.
2. equipment: essential medical devices, diagnostic or therapeutic machines, and hardware.
3. specialties: hospital departments and specialist designations (e.g. Nephrology, CTVS, Medical Oncology).
4. capabilities: facility units, operational infrastructure (e.g. Dialysis unit, Cath Lab, ICU, Daycare OT).

Output strictly valid JSON with this schema:
{
  "service_name": string,
  "clinical_synonyms": [string, ...],
  "procedures": [string, ...],
  "equipment": [string, ...],
  "specialties": [string, ...],
  "capabilities": [string, ...]
}"""


class ExpandedServiceRequirements(BaseModel):
    service_name: str
    clinical_synonyms: list[str] = Field(default_factory=list, description="Medical terms, abbreviations, and common synonyms")
    procedures: list[str] = Field(default_factory=list, description="Specific surgical or clinical procedures")
    equipment: list[str] = Field(default_factory=list, description="Essential medical equipment, instruments, or machines")
    specialties: list[str] = Field(default_factory=list, description="Medical departments or specialist doctor designations")
    capabilities: list[str] = Field(default_factory=list, description="Operational capabilities, specialized units, ICUs, or certifications")
    is_cached: bool = False


class QueryExpander:
    def __init__(self, cache_path: Path = CACHE_FILE, endpoint: Optional[str] = None):
        self.cache_path = cache_path
        self.endpoint = endpoint or Config().llm_endpoint
        self._cache: dict[str, dict] = self._load_cache()
        self._client = None

    def _load_cache(self) -> dict[str, dict]:
        if self.cache_path.exists():
            try:
                return json.loads(self.cache_path.read_text(encoding="utf-8"))
            except Exception as e:
                log.warning("Could not read query expansion cache: %s", e)
        return {}

    def _save_cache(self) -> None:
        try:
            self.cache_path.write_text(json.dumps(self._cache, indent=2), encoding="utf-8")
        except Exception as e:
            log.warning("Could not persist query expansion cache: %s", e)

    def _get_client(self):
        if self._client is None:
            from ..rag import _workspace
            self._client = _workspace().serving_endpoints.get_open_ai_client()
        return self._client

    def _heuristic_fallback(self, query: str) -> ExpandedServiceRequirements:
        """Deterministic medical root expansion when offline or if LLM fails."""
        clean = query.strip()
        name = clean.replace("_", " ").title()
        toks = re.findall(r"\b[a-zA-Z]{3,}\b", clean.lower())

        synonyms = [clean, name]
        procs = [clean]
        equip = [clean]
        specs = []
        caps = [clean]

        # Domain heuristic mappings for lay words
        q_low = clean.lower()
        if any(w in q_low for w in ("dialysis", "kidney", "renal")):
            synonyms.extend(["renal replacement", "hemodialysis", "dialysis", "hemofiltration"])
            procs.extend(["hemodialysis", "crrt", "renal dialysis"])
            equip.extend(["dialyzer", "dialysis machine", "ro water plant"])
            specs.extend(["nephrology", "renal care", "urology"])
            caps.extend(["dialysis unit", "renal care unit"])
        elif any(w in q_low for w in ("bypass", "heart", "cardio", "stent", "angio")):
            synonyms.extend(["coronary artery bypass graft", "cabg", "angioplasty", "cardiac surgery"])
            procs.extend(["cabg", "angioplasty", "coronary angiography"])
            equip.extend(["heart lung machine", "cath lab", "iabp"])
            specs.extend(["cardiology", "cardiothoracic surgery", "ctvs"])
            caps.extend(["cardiac icu", "ccu", "cath lab"])
        elif any(w in q_low for w in ("cancer", "chemo", "tumor", "onco")):
            synonyms.extend(["chemotherapy", "medical oncology", "infusion daycare"])
            procs.extend(["chemotherapy", "systemic therapy", "infusion"])
            equip.extend(["infusion pump", "laminar airflow", "biosafety cabinet"])
            specs.extend(["medical oncology", "oncology", "surgical oncology"])
            caps.extend(["chemotherapy daycare", "oncology center"])
        elif any(w in q_low for w in ("cataract", "eye", "lens", "vision")):
            synonyms.extend(["phacoemulsification", "cataract removal", "lens replacement"])
            procs.extend(["phacoemulsification", "mics", "iol implantation"])
            equip.extend(["phaco machine", "ophthalmic microscope"])
            specs.extend(["ophthalmology", "eye care"])
            caps.extend(["ophthalmic ot", "eye daycare"])
        elif any(w in q_low for w in ("bone", "fracture", "ortho")):
            synonyms.extend(["orthopedic trauma", "fracture reduction", "plaster"])
            procs.extend(["closed reduction", "internal fixation", "casting"])
            equip.extend(["c-arm fluoroscopy", "x-ray", "plaster table"])
            specs.extend(["orthopedics", "trauma surgery", "emergency medicine"])
            caps.extend(["orthopedic emergency", "trauma ot"])
        else:
            specs.append(clean)

        return ExpandedServiceRequirements(
            service_name=name,
            clinical_synonyms=list(dict.fromkeys(synonyms)),
            procedures=list(dict.fromkeys(procs)),
            equipment=list(dict.fromkeys(equip)),
            specialties=list(dict.fromkeys(specs)),
            capabilities=list(dict.fromkeys(caps)),
            is_cached=False,
        )

    def expand(self, query: str, use_llm: bool = True) -> ExpandedServiceRequirements:
        key = query.lower().strip()
        if not key:
            return ExpandedServiceRequirements(service_name="Unknown Service")

        # 1. Check Cache
        if key in self._cache:
            data = dict(self._cache[key])
            data["is_cached"] = True
            return ExpandedServiceRequirements(**data)

        # 2. Try LLM Generation if enabled
        if use_llm:
            try:
                client = self._get_client()
                resp = client.chat.completions.create(
                    model=self.endpoint,
                    temperature=0.0,
                    max_tokens=600,
                    timeout=10.0,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": f"Decompose healthcare service: '{query}'"}
                    ],
                    response_format={"type": "json_object"}
                )
                content = resp.choices[0].message.content or ""
                # Strip code fences if present
                clean_json = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
                parsed = json.loads(clean_json)
                
                exp = ExpandedServiceRequirements(
                    service_name=parsed.get("service_name") or query.title(),
                    clinical_synonyms=parsed.get("clinical_synonyms") or [query],
                    procedures=parsed.get("procedures") or [],
                    equipment=parsed.get("equipment") or [],
                    specialties=parsed.get("specialties") or [],
                    capabilities=parsed.get("capabilities") or [],
                    is_cached=True,
                )
                self._cache[key] = exp.model_dump()
                self._save_cache()
                return exp
            except Exception as e:
                log.info("LLM query expansion unavailable or failed (%s); using heuristic fallback", e)

        # 3. Fallback to heuristic decomposition
        fallback = self._heuristic_fallback(query)
        self._cache[key] = fallback.model_dump()
        self._save_cache()
        return fallback


_GLOBAL_EXPANDER = QueryExpander()


def expand_service_query(query: str, use_llm: bool = True) -> ExpandedServiceRequirements:
    return _GLOBAL_EXPANDER.expand(query, use_llm=use_llm)
