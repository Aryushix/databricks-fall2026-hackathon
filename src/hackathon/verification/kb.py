"""Treatment knowledge base: load the reviewed YAML entries and tag claims with them.

The tagger is pattern-based and deliberately conservative. Confusables are matched first and block any
overlapping positive match ("CT coronary angiography" never counts as coronary angiography). Among the
remaining matches the longest wins, so "cath lab" is a prerequisite, not the procedure. Ambiguous terms
("angiography" with no qualifier) are settled by nearby context or left AMBIGUOUS.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import yaml

from .records import ConceptMatch

KB_DIR = Path(__file__).resolve().parent / "kb"
_FLAGS = re.IGNORECASE
_CONTEXT_CHARS = 100         # how far around an ambiguous term to look for context
_ROLE_RANK = {"exact": 0, "child": 1, "prerequisite": 2, "ambiguous": 3}


def _compile(patterns) -> list[re.Pattern]:
    return [re.compile(p, _FLAGS) for p in (patterns or [])]


@dataclass
class _Rule:
    role: str
    sub_id: str
    kind: str
    specificity: float
    patterns: list[re.Pattern]
    key: bool = False
    negates_parent: bool = True


@dataclass
class KBEntry:
    concept_id: str
    kb_version: str
    canonical_name: str
    category: str
    specialization: str
    review_status: str
    plausible_facility_types: list[str]
    rules: list[_Rule]
    confusables: list[tuple[str, list[re.Pattern]]]
    ambiguous: list[tuple[re.Pattern, re.Pattern | None, re.Pattern | None]]
    prerequisites: dict[str, dict]
    taxonomy: list[tuple[str, bool, list[re.Pattern]]]
    key_groups: list[list[str]]
    codes: dict = field(default_factory=dict)
    examples: list[dict] = field(default_factory=list)
    raw_hash: str = ""

    @classmethod
    def from_dict(cls, d: dict, raw_hash: str = "") -> KBEntry:
        rules = [_Rule("direct", "synonym", "exact", 1.0, _compile(d.get("synonyms")))]
        for child in d.get("children") or []:
            # A negative claim about a child ("does not do PCI") says nothing about the parent unless the
            # entry says the child is just another name for it ("dialysis not available").
            rules.append(_Rule("direct", child["id"], "child", float(child.get("specificity", 0.95)),
                               _compile(child.get("patterns")),
                               negates_parent=bool(child.get("negates_parent", False))))
        prereqs, taxonomy = {}, []
        for p in d.get("prerequisites") or []:
            prereqs[p["id"]] = {"kind": p.get("kind"), "key": bool(p.get("key"))}
            if p.get("patterns"):
                rules.append(_Rule("prerequisite", p["id"], "prerequisite", 1.0, _compile(p["patterns"]),
                                   key=bool(p.get("key"))))
            if p.get("taxonomy"):
                taxonomy.append((p["id"], bool(p.get("key")), _compile(p["taxonomy"])))
        ambiguous = [(re.compile(a["pattern"], _FLAGS),
                      re.compile(a["upgrade_if"], _FLAGS) if a.get("upgrade_if") else None,
                      re.compile(a["confusable_if"], _FLAGS) if a.get("confusable_if") else None)
                     for a in d.get("ambiguous") or []]
        return cls(
            concept_id=d["concept_id"], kb_version=str(d["kb_version"]),
            canonical_name=d["canonical_name"], category=d.get("category", ""),
            specialization=d.get("specialization", "advanced"),
            review_status=d.get("review_status", "draft"),
            plausible_facility_types=list(d.get("plausible_facility_types") or ["hospital"]),
            rules=rules,
            confusables=[(c["id"], _compile(c["patterns"])) for c in d.get("confusables") or []],
            ambiguous=ambiguous, prerequisites=prereqs, taxonomy=taxonomy,
            key_groups=[list(g) for g in d.get("key_groups") or []],
            codes=d.get("codes") or {}, examples=list(d.get("examples") or []), raw_hash=raw_hash,
        )

    def key_prerequisites_met(self, present: set[str]) -> bool:
        return bool(self.key_groups) and all(any(p in present for p in g) for g in self.key_groups)

    def key_prerequisite_ids(self) -> list[str]:
        return [p for g in self.key_groups for p in g]


def load_kb(concepts=None, kb_dir: Path = KB_DIR) -> dict[str, KBEntry]:
    entries = {}
    for path in sorted(Path(kb_dir).glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        d = yaml.safe_load(text)
        if concepts and d["concept_id"] not in concepts:
            continue
        entries[d["concept_id"]] = KBEntry.from_dict(d, hashlib.sha1(text.encode()).hexdigest()[:10])
    missing = set(concepts or []) - set(entries)
    if missing:
        raise ValueError(f"no KB entry for {sorted(missing)} in {kb_dir}")
    return entries


def kb_version(entries: dict[str, KBEntry]) -> str:
    """One string naming every entry's version and content hash, e.g. 'cardiac_catheterization@0.2.0#ab12…'."""
    return ";".join(f"{e.concept_id}@{e.kb_version}#{e.raw_hash}" for e in entries.values())


class ConceptTagger(Protocol):
    def tag(self, text: str, field: str) -> list[ConceptMatch]: ...


def _overlaps(a, b) -> bool:
    return a[0] < b[1] and b[0] < a[1]


class PatternTagger:
    """Tags a claim with every KB concept, child, prerequisite or confusable it mentions."""

    def __init__(self, entries: dict[str, KBEntry]):
        self.entries = entries

    def tag(self, text: str, field: str) -> list[ConceptMatch]:
        if field == "specialties":
            return self._tag_taxonomy(text)
        out = []
        for e in self.entries.values():
            out.extend(self._tag_entry(e, text))
        return out

    def _tag_taxonomy(self, label: str) -> list[ConceptMatch]:
        out = []
        for e in self.entries.values():
            for pid, key, pats in e.taxonomy:
                if any(p.search(label) for p in pats):
                    out.append(ConceptMatch(e.concept_id, "prerequisite", pid, "prerequisite", 1.0,
                                            0, len(label), label, key=key))
        return out

    def _tag_entry(self, e: KBEntry, text: str) -> list[ConceptMatch]:
        blocked, out = [], []
        for cid, pats in e.confusables:
            for p in pats:
                for m in p.finditer(text):
                    blocked.append((m.start(), m.end()))
                    out.append(ConceptMatch(e.concept_id, "confusable", cid, "confusable", 0.0,
                                            m.start(), m.end(), m.group(0)))
        cands = []
        for r in e.rules:
            for p in r.patterns:
                for m in p.finditer(text):
                    cands.append((m.start(), m.end(), r.kind, r))
        for pat, up, conf in e.ambiguous:
            for m in pat.finditer(text):
                cands.append((m.start(), m.end(), "ambiguous", (pat, up, conf)))
        cands = [c for c in cands if not any(_overlaps(c[:2], b) for b in blocked)]
        cands.sort(key=lambda c: (-(c[1] - c[0]), _ROLE_RANK[c[2]], c[0]))
        kept = []
        for c in cands:
            if not any(_overlaps(c[:2], k[:2]) for k in kept):
                kept.append(c)
        for s, t, kind, r in sorted(kept, key=lambda c: c[0]):
            if kind == "ambiguous":
                _, up, conf = r
                ctx = text[max(0, s - _CONTEXT_CHARS):s] + " " + text[t:t + _CONTEXT_CHARS]
                if conf is not None and conf.search(ctx):
                    out.append(ConceptMatch(e.concept_id, "confusable", "ambiguous_in_confusable_context",
                                            "confusable", 0.0, s, t, text[s:t]))
                elif up is not None and up.search(ctx):
                    out.append(ConceptMatch(e.concept_id, "direct", "ambiguous_upgraded_by_context",
                                            "child", 0.85, s, t, text[s:t], negates_parent=False))
                else:
                    out.append(ConceptMatch(e.concept_id, "ambiguous", "ambiguous", "ambiguous", 0.6,
                                            s, t, text[s:t], negates_parent=False))
            else:
                out.append(ConceptMatch(e.concept_id, r.role, r.sub_id, kind, r.specificity, s, t, text[s:t],
                                        key=r.key, negates_parent=r.negates_parent if kind == "child" else True))
        return out
