"""Deterministic red-flag screen. Runs first, on the raw message, with no model and no facility data.

Rules live in configs/red_flags.yaml (versioned, review_status: draft). A rule fires when each of its
pattern groups has a match that no negation cue covers. The screen can only set a floor: later stages
may raise urgency, never lower it. Nothing here raises on any string input.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RULES = REPO_ROOT / "configs" / "red_flags.yaml"

LEVELS = ("routine", "urgent", "emergency")      # ordered; "unclear" is not on this scale
_FRAGMENT = re.compile(r"<<([A-Z_]+)>>")


def level_rank(level: str | None) -> int:
    return LEVELS.index(level) if level in LEVELS else -1


def higher(a: str | None, b: str | None) -> str | None:
    """The more urgent of two levels (None and "unclear" lose to any level)."""
    return a if level_rank(a) >= level_rank(b) else b


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "")
    t = t.replace("’", "'").replace("‘", "'").replace("`", "'")
    return re.sub(r"\s+", " ", t).strip().lower()


@dataclass(frozen=True)
class FiredRule:
    rule_id: str
    category: str
    floor: str
    crisis: bool = False


@dataclass(frozen=True)
class Screen:
    fired: tuple[FiredRule, ...]
    rules_version: str

    @property
    def floor(self) -> str | None:
        out = None
        for f in self.fired:
            out = higher(out, f.floor)
        return out

    @property
    def rule_ids(self) -> list[str]:
        return [f.rule_id for f in self.fired]

    @property
    def categories(self) -> list[str]:
        return list(dict.fromkeys(f.category for f in self.fired))

    @property
    def crisis(self) -> bool:
        return any(f.crisis for f in self.fired)

    @property
    def emergency(self) -> bool:
        return self.floor == "emergency"


@dataclass
class _Rule:
    rule_id: str
    category: str
    floor: str
    groups: list[tuple[list[re.Pattern], bool]]     # (patterns, negatable)
    crisis: bool


class RedFlagRules:
    def __init__(self, cfg: dict):
        self.version = str(cfg["rules_version"])
        self.review_status = cfg.get("review_status", "draft")
        defs = cfg.get("defs", {})

        def expand(p: str) -> str:
            for _ in range(5):                     # fragments may nest a little
                p2 = _FRAGMENT.sub(lambda m: defs[m.group(1)], p)
                if p2 == p:
                    return p
                p = p2
            return p

        def comp(p: str) -> re.Pattern:
            return re.compile(expand(p), re.IGNORECASE)

        neg = cfg["negation"]
        self._before = comp(neg["before"])
        self._after = comp(neg["after"])
        self._cancel = comp(neg["cancel_after_cue"])
        self._bounds = comp(neg["boundaries"])
        self._wb, self._wa = int(neg["before_window_chars"]), int(neg["after_window_chars"])
        self.rules: list[_Rule] = []
        seen = set()
        for r in cfg["rules"]:
            if r["id"] in seen:
                raise ValueError(f"duplicate red-flag rule id {r['id']}")
            seen.add(r["id"])
            if r["floor"] not in ("urgent", "emergency"):
                raise ValueError(f"{r['id']}: floor must be urgent or emergency")
            rule_negatable = bool(r.get("negatable", True))
            groups = []
            for g in r.get("all_of") or [r.get("patterns") or []]:
                pats, neg = (g["patterns"], bool(g.get("negatable", True))) if isinstance(g, dict) else (g, True)
                if not pats:
                    raise ValueError(f"{r['id']}: empty pattern group")
                groups.append(([comp(p) for p in pats], rule_negatable and neg))
            if not groups:
                raise ValueError(f"{r['id']}: needs patterns or all_of")
            self.rules.append(_Rule(r["id"], r["category"], r["floor"], groups, bool(r.get("crisis", False))))

    @classmethod
    def load(cls, path: str | Path = DEFAULT_RULES) -> RedFlagRules:
        return cls(yaml.safe_load(Path(path).read_text(encoding="utf-8")))

    # ------------------------------------------------------------------ negation
    def _negated(self, text: str, start: int, end: int) -> bool:
        before = text[max(0, start - self._wb):start]
        cuts = list(self._bounds.finditer(before))
        if cuts:
            before = before[cuts[-1].end():]
        for cue in self._before.finditer(before):
            if not self._cancel.match(before[cue.end():]):
                return True
        after = text[end:end + self._wa]
        cut = self._bounds.search(after)
        if cut:
            after = after[:cut.start()]
        return bool(self._after.search(after))

    def is_negated(self, text: str, start: int, end: int) -> bool:
        """Scoped negation on normalised text (shared with the rule extractor)."""
        return self._negated(text, start, end)

    def _group_hit(self, text: str, group: list[re.Pattern], negatable: bool) -> bool:
        for pat in group:
            for m in pat.finditer(text):
                if m.end() == m.start():
                    continue
                if not negatable or not self._negated(text, m.start(), m.end()):
                    return True
        return False

    def screen(self, message: str) -> Screen:
        text = normalize(message)
        fired = []
        for r in self.rules:
            if all(self._group_hit(text, pats, neg) for pats, neg in r.groups):
                fired.append(FiredRule(r.rule_id, r.category, r.floor, r.crisis))
        return Screen(tuple(fired), self.version)
