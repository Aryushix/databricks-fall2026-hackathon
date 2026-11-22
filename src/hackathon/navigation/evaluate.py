"""Evaluation for symptom navigation (CLI: tools/eval_navigation.py).

These metrics measure consistency with the verifier and with the draft configs, not clinical
correctness. There is no ground truth for "the right facility"; the human-judgment export is the
honest proxy for recommendation precision.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Iterable, Optional

import yaml

from .contract import RecommendRequest, RecommendResponse
from .red_flags import REPO_ROOT

DEFAULT_CASES = REPO_ROOT / "eval" / "navigation" / "cases.yaml"
RED_FLAG_CASES = REPO_ROOT / "tests" / "fixtures" / "navigation" / "red_flag_cases.yaml"
LIST_FIELDS = ("body_locations", "associated_symptoms", "special_context")
URGENCY_ORDER = {"unclear": -1, "routine": 0, "urgent": 1, "emergency": 2}

# Fallback patient points (state capitals) when the India Post directory isn't installed.
CAPITALS = [("Patna", 25.5941, 85.1376), ("Lucknow", 26.8467, 80.9462), ("Mumbai", 19.0760, 72.8777),
            ("Kolkata", 22.5726, 88.3639), ("Chennai", 13.0827, 80.2707), ("Bengaluru", 12.9716, 77.5946),
            ("Hyderabad", 17.3850, 78.4867), ("Jaipur", 26.9124, 75.7873), ("Bhopal", 23.2599, 77.4126),
            ("Raipur", 21.2514, 81.6296), ("Ranchi", 23.3441, 85.3096), ("Bhubaneswar", 20.2961, 85.8245),
            ("Guwahati", 26.1445, 91.7362), ("Thiruvananthapuram", 8.5241, 76.9366), ("Chandigarh", 30.7333, 76.7794),
            ("Shimla", 31.1048, 77.1734), ("Dehradun", 30.3165, 78.0322), ("Gandhinagar", 23.2156, 72.6369),
            ("Srinagar", 34.0837, 74.7973), ("Imphal", 24.8170, 93.9368)]


def load_cases(path: str | Path = DEFAULT_CASES) -> list[dict]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))["cases"]


def _request(message: str, lat: float = 19.0760, lon: float = 72.8777, k: int = 5) -> RecommendRequest:
    return RecommendRequest(patient_message=message, latitude=lat, longitude=lon, max_results=k)


# ---------------------------------------------------------------------- extraction
def extraction_accuracy(cases: list[dict], extractor) -> dict:
    per: dict = {}
    for c in cases:
        a = extractor.extract(c["message"])
        for field, want in (c.get("extraction") or {}).items():
            got = getattr(a, field)
            ok = set(got) == set(want) if field in LIST_FIELDS else got == want
            s = per.setdefault(field, {"n": 0, "correct": 0, "misses": []})
            s["n"] += 1
            s["correct"] += int(ok)
            if not ok:
                s["misses"].append({"case": c["id"], "want": want, "got": got})
    for s in per.values():
        s["accuracy"] = round(s["correct"] / s["n"], 3) if s["n"] else None
    return per


# ---------------------------------------------------------------------- triage and care needs
def triage_and_needs(cases: list[dict], navigator) -> dict:
    """Runs each case through the real service (screen, extraction, urgency, care needs)."""
    urg = {"n": 0, "exact": 0, "under": [], "over": []}
    emerg = {"n": 0, "hit": 0, "misses": []}
    pr = {role: {"tp": 0, "fp": 0, "fn": 0} for role in ("required", "supporting", "all")}
    need_misses = []
    for c in cases:
        r = navigator.recommend(_request(c["message"]))
        u, want = r.triage.urgency, c["urgency"]
        urg["n"] += 1
        urg["exact"] += int(u == want)
        if URGENCY_ORDER[u] < URGENCY_ORDER[want]:
            urg["under"].append({"case": c["id"], "want": want, "got": u})
        elif URGENCY_ORDER[u] > URGENCY_ORDER[want]:
            urg["over"].append({"case": c["id"], "want": want, "got": u})
        if want == "emergency":
            emerg["n"] += 1
            emerg["hit"] += int(u == "emergency" and r.emergency is not None)
            if u != "emergency":
                emerg["misses"].append(c["id"])
        got = {role: {n.id for n in r.care_needs if n.role == role} for role in ("required", "supporting")}
        for role in ("required", "supporting"):
            w = set(c.get(role) or [])
            pr[role]["tp"] += len(got[role] & w)
            pr[role]["fp"] += len(got[role] - w)
            pr[role]["fn"] += len(w - got[role])
            if got[role] != w:
                need_misses.append({"case": c["id"], "role": role, "want": sorted(w), "got": sorted(got[role])})
        allw, allg = set(c.get("required") or []) | set(c.get("supporting") or []), got["required"] | got["supporting"]
        pr["all"]["tp"] += len(allg & allw)
        pr["all"]["fp"] += len(allg - allw)
        pr["all"]["fn"] += len(allw - allg)
    for s in pr.values():
        s["precision"] = round(s["tp"] / (s["tp"] + s["fp"]), 3) if s["tp"] + s["fp"] else None
        s["recall"] = round(s["tp"] / (s["tp"] + s["fn"]), 3) if s["tp"] + s["fn"] else None
    urg["accuracy"] = round(urg["exact"] / urg["n"], 3) if urg["n"] else None
    emerg["recall"] = round(emerg["hit"] / emerg["n"], 3) if emerg["n"] else None
    return {"urgency": urg, "emergency_recall_cases": emerg, "care_needs": pr, "care_need_misses": need_misses}


def red_flag_set(rules, path: str | Path = RED_FLAG_CASES) -> dict:
    cases = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    pos = [(cat, m) for cat, ms in cases["positives"].items() for m in ms]
    hits = [cat in rules.screen(m).categories for cat, m in pos]
    neg_fired = [m for m in cases["negatives"] if rules.screen(m).fired]
    return {"positives": len(pos), "recall": round(sum(hits) / len(pos), 4),
            "missed": [m for (cat, m), h in zip(pos, hits) if not h],
            "negatives": len(cases["negatives"]), "over_triage_rate": round(len(neg_fired) / len(cases["negatives"]), 3),
            "over_triaged": neg_fired}


# ---------------------------------------------------------------------- capability and coverage
def sample_locations(per_state: int = 2, seed: int = 20261122) -> list[tuple[str, float, float]]:
    """Synthetic patient points: India Post offices (public directory) sampled per state, or state capitals."""
    try:
        from .. import location_check as lc
        d = lc.normalize_directory(lc.load_indiapins()).dropna(subset=["lat", "lon"])
    except Exception:
        return list(CAPITALS)
    d = d[d.lat.between(6, 37.5) & d.lon.between(68, 97.5)]
    rnd = random.Random(seed)
    out = []
    for state, g in sorted(d.groupby("state"), key=lambda x: str(x[0])):
        rows = g.sample(n=min(per_state, len(g)), random_state=rnd.randint(0, 10**6))
        out += [(f"{state}: {r.office}", float(r.lat), float(r.lon)) for r in rows.itertuples()]
    return out


def capability_and_coverage(cases: list[dict], navigator, locations: Iterable[tuple[str, float, float]],
                            eligible=("CONFIRMED", "LIKELY")) -> tuple[dict, list[tuple[dict, str, RecommendResponse]]]:
    checked, violations, responses = 0, [], []
    cov: dict = {}
    for c in cases:
        if c["urgency"] == "unclear":
            continue
        for label, lat, lon in locations:
            r = navigator.recommend(_request(c["message"], lat, lon))
            responses.append((c, label, r))
            for rec in r.recommendations:
                checked += 1
                statuses = [m.status for m in rec.matched_care_needs]
                bad = (rec.list == "verified" and not rec.capability_match) \
                    or (rec.list == "unverified_fallback" and rec.capability_match) \
                    or any(s not in eligible for s in statuses) or "CONTRADICTED" in statuses
                if bad:
                    violations.append({"case": c["id"], "location": label, "hospital_id": rec.hospital_id})
            assessable = any(n.verification_kb or n.proxy_kbs for n in r.care_needs if n.role == "required")
            if assessable and r.search is not None:
                s = cov.setdefault(c["id"], {"locations": 0, "within_radius": 0, "after_widening": 0})
                s["locations"] += 1
                has_verified = any(x.capability_match for x in r.recommendations)
                s["within_radius"] += int(has_verified and not r.search.widened)
                s["after_widening"] += int(has_verified)
    for s in cov.values():
        s["share_within_radius"] = round(s["within_radius"] / s["locations"], 3)
        s["share_after_widening"] = round(s["after_widening"] / s["locations"], 3)
    return {"recommendations_checked": checked, "violations": violations,
            "consistency": round(1 - len(violations) / checked, 4) if checked else None,
            "coverage_by_case": cov}, responses


def judgment_rows(responses: list[tuple[dict, str, RecommendResponse]], n_cases: int = 25, top_k: int = 5) -> list[dict]:
    """Top-k results for up to n_cases (case, location) pairs: each case at a different location, skipping
    responses with no results, so the set spans many places."""
    by_case: dict = {}
    for c, label, r in responses:
        if r.recommendations:
            by_case.setdefault(c["id"], []).append((c, label, r))
    picked = []
    for k, (cid, v) in enumerate(sorted(by_case.items())):
        if len(picked) >= n_cases:
            break
        picked.append(v[(k * 7) % len(v)])
    rows = []
    for c, label, r in picked:
        for rec in r.recommendations[:top_k]:
            rows.append({"case_id": c["id"], "message_synthetic": c["message"], "location": label,
                         "urgency": r.triage.urgency, "rank": rec.rank, "list": rec.list, "hospital_id": rec.hospital_id,
                         "hospital_name": rec.hospital_name, "facility_type": rec.facility_type,
                         "straight_line_distance_km": rec.straight_line_distance_km,
                         "matched": "; ".join(f"{m.kb_id}={m.status}" for m in rec.matched_care_needs),
                         "explanation": rec.explanation, "warnings": " | ".join(rec.warnings),
                         "judge_1_appropriate (y/n/unsure)": "", "judge_2_appropriate (y/n/unsure)": "", "notes": ""})
    return rows


def report_markdown(m: dict) -> str:
    rf, tn = m["red_flags"], m["triage_and_needs"]
    lines = ["# Symptom navigation: evaluation report", "",
             "These metrics measure consistency with the verifier and the draft configs, **not clinical correctness**. "
             "All cases and messages are synthetic.", "",
             f"- Extractor: `{m['extractor']}`",
             f"- Red-flag set: recall **{rf['recall']:.0%}** on {rf['positives']} phrasings (CI gate: 100%); "
             f"over-triage {rf['over_triage_rate']:.0%} on {rf['negatives']} non-emergency phrasings",
             f"- Emergency recall on labeled cases: **{tn['emergency_recall_cases']['recall']}**; "
             f"urgency accuracy {tn['urgency']['accuracy']}; under-triaged {len(tn['urgency']['under'])}, "
             f"over-triaged {len(tn['urgency']['over'])}",
             f"- Care needs: precision {tn['care_needs']['all']['precision']}, recall {tn['care_needs']['all']['recall']} "
             f"(required: {tn['care_needs']['required']['precision']} / {tn['care_needs']['required']['recall']})", "",
             "## Extraction accuracy by field", "", "| Field | Cases | Accuracy |", "| --- | ---: | ---: |"]
    lines += [f"| {f} | {s['n']} | {s['accuracy']} |" for f, s in sorted(m["extraction"].items())]
    if m.get("capability"):
        cap = m["capability"]
        lines += ["", "## Capability consistency and coverage", "",
                  f"Consistency: **{cap['consistency']}** over {cap['recommendations_checked']} recommendations "
                  "(share whose matched status is in the allowed set; anything below 1.0 is a bug).", "",
                  "| Case | Locations | Verified match within radius | After one widening |", "| --- | ---: | ---: | ---: |"]
        lines += [f"| {cid} | {s['locations']} | {s['share_within_radius']:.0%} | {s['share_after_widening']:.0%} |"
                  for cid, s in sorted(cap["coverage_by_case"].items())]
    return "\n".join(lines) + "\n"


def build_components(use_llm: bool = False, data_paths: Optional[dict] = None, with_data: bool = True):
    from .service import build_navigator
    nav = build_navigator(data_paths=data_paths, use_llm=use_llm)
    if not with_data:
        nav.store_provider = None
    return nav
