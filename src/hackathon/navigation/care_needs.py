"""Findings and red flags -> urgency and care needs. Deterministic, driven by configs/care_needs.yaml.

A care need's verifier KB counts only if src/hackathon/verification/kb/<kb>.yaml exists; adding a KB
file therefore lights up the need here without code changes (the ranker also checks the loaded
verification run has records for it).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from ..verification.kb import load_kb
from .contract import CareNeed, SymptomAnalysis
from .extract import DEFAULT_TERMS, Terms
from .red_flags import REPO_ROOT, Screen, higher

DEFAULT_CARE_NEEDS = REPO_ROOT / "configs" / "care_needs.yaml"


def _holds(when: dict, a: SymptomAnalysis) -> bool:
    checks = {
        "locations_any": lambda v: any(x in a.body_locations for x in v),
        "locations_prefix": lambda v: any(loc.startswith(p) for loc in a.body_locations for p in v),
        "severity_any": lambda v: a.severity in v,
        "onset_any": lambda v: a.onset in v,
        "context_any": lambda v: any(x in a.special_context for x in v),
        "symptoms_any": lambda v: any(x in a.associated_symptoms for x in v),
    }
    return all(checks[k](v) for k, v in when.items())


class CareNeedTable:
    def __init__(self, cfg: dict, terms: Terms, kb_ids: set[str]):
        self.cfg, self.terms, self.kb_ids = cfg, terms, set(kb_ids)
        self.version = str(cfg["care_needs_version"])
        self.needs: dict = cfg["needs"]
        for cat, spec in cfg["categories"].items():
            for nid in spec.get("required", []) + spec.get("supporting", []):
                if nid not in self.needs:
                    raise ValueError(f"category {cat} uses unknown care need {nid}")
        for f in cfg["findings"]:
            for nid in f.get("required", []) + f.get("supporting", []):
                if nid not in self.needs:
                    raise ValueError(f"finding {f['id']} uses unknown care need {nid}")

    @classmethod
    def load(cls, path: str | Path = DEFAULT_CARE_NEEDS, terms_path: str | Path = DEFAULT_TERMS,
             kb_ids: Optional[set[str]] = None) -> CareNeedTable:
        cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls(cfg, Terms.load(terms_path), set(load_kb()) if kb_ids is None else kb_ids)

    # ------------------------------------------------------------------ lookups
    def name(self, nid: str) -> str:
        return self.needs[nid].get("name", nid)

    def verification_kb(self, nid: str) -> Optional[str]:
        kb = self.needs[nid].get("kb")
        return kb if kb in self.kb_ids else None

    def proxy_kbs(self, nid: str) -> list[str]:
        return [k for k in self.needs[nid].get("proxy_kbs", []) if k in self.kb_ids]

    def signals(self, nid: str) -> dict:
        return self.needs[nid].get("signals", {})

    def fallback_types(self, nid: str) -> list[str]:
        return self.needs[nid].get("fallback_types", ["hospital"])

    # ------------------------------------------------------------------ mapping
    def _finding_needs(self, a: Optional[SymptomAnalysis]) -> list[tuple[str, str]]:
        if a is None:
            return []
        out, used_groups = [], set()
        for f in self.cfg["findings"]:
            g = f.get("group")
            if g and g in used_groups:
                continue
            if _holds(f["when"], a):
                if g:
                    used_groups.add(g)
                out += [(n, "required") for n in f.get("required", [])] + [(n, "supporting") for n in f.get("supporting", [])]
        return out

    def _category_needs(self, cats: list[str]) -> list[tuple[str, str]]:
        out = []
        for c in cats:
            spec = self.cfg["categories"].get(c, {})
            out += [(n, "required") for n in spec.get("required", [])] + [(n, "supporting") for n in spec.get("supporting", [])]
        return out

    def urgency(self, analysis: Optional[SymptomAnalysis], screen: Optional[Screen]) -> str:
        """Urgency from extraction alone; the service combines it with the rule floor (raise only)."""
        if analysis is None or analysis.language.startswith("unsupported"):
            return "unclear"
        level = "emergency" if analysis.danger_signs else None
        for r in self.cfg["urgency_rules"]:
            if _holds(r["when"], analysis):
                level = higher(level, r["level"])
        if level is None and not self._finding_needs(analysis):
            return "unclear"
        return level or "routine"

    def care_needs(self, analysis: Optional[SymptomAnalysis], screen: Optional[Screen], urgency: str) -> list[CareNeed]:
        cats = list(screen.categories if screen else []) + list(analysis.danger_signs if analysis else [])
        pairs = self._category_needs(list(dict.fromkeys(cats))) or self._finding_needs(analysis)
        roles: dict[str, str] = {}
        for nid, role in pairs:
            if roles.get(nid) != "required":
                roles[nid] = role
        ordered = [n for n, r in roles.items() if r == "required"] + [n for n, r in roles.items() if r == "supporting"]
        return [CareNeed(id=n, role=roles[n], verification_kb=self.verification_kb(n), proxy_kbs=self.proxy_kbs(n))
                for n in ordered]

    def follow_up_questions(self, analysis: Optional[SymptomAnalysis]) -> list[str]:
        q = self.cfg["follow_up_questions"]
        if analysis is not None and analysis.language.startswith("unsupported"):
            return [q["language"]]
        missing = analysis.missing_information if analysis else ["body_location", "severity", "duration"]
        order = [("body_location", "location"), ("severity", "severity"), ("duration", "duration"), ("onset", "onset")]
        return [q[key] for field, key in order if field in missing][:3]
