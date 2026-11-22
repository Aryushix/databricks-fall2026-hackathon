"""Dense vector embedding and semantic cosine similarity engine for clinical services and claims."""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from typing import Optional

import numpy as np

from ..config import Config

log = logging.getLogger("hackathon.navigation.embeddings")

# Core clinical synonym mapping for zero-network environments
CLINICAL_SYNONYM_PAIRS = [
    ({"kidney", "renal", "nephrology", "dialysis", "hemodialysis", "haemodialysis"}, 0.90),
    ({"heart", "cardiac", "cardiology", "bypass", "cabg", "ctvs", "angioplasty"}, 0.90),
    ({"cancer", "oncology", "chemo", "chemotherapy", "antineoplastic", "tumor"}, 0.90),
    ({"cataract", "phacoemulsification", "iol", "eye", "ophthalmology"}, 0.90),
    ({"bone", "fracture", "orthopedic", "orthopaedics", "orif", "plaster"}, 0.85),
    ({"brain", "neurology", "neurosurgery", "stroke", "tpa"}, 0.85),
    ({"maternity", "delivery", "cesarean", "c-section", "obstetrics", "gynecology"}, 0.90),
    ({"baby", "infant", "neonatal", "nicu", "pediatric"}, 0.90),
    ({"imaging", "radiology", "scan", "x-ray", "mri", "ct scan"}, 0.85),
    ({"emergency", "trauma", "casualty", "resuscitation", "ambulance"}, 0.85),
]


def _clean_tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"\b[a-zA-Z]{3,}\b", text.lower())
            if t not in {"and", "the", "for", "with", "unit", "care", "services", "hospital", "clinic"}]


def _char_ngrams(text: str, n: int = 3) -> Counter:
    clean = re.sub(r"[^a-zA-Z]", "", text.lower())
    if len(clean) < n:
        return Counter([clean] if clean else [])
    return Counter(clean[i : i + n] for i in range(len(clean) - n + 1))


def _cosine_counters(c1: Counter, c2: Counter) -> float:
    if not c1 or not c2:
        return 0.0
    common = set(c1) & set(c2)
    dot = sum(c1[k] * c2[k] for k in common)
    mag1 = math.sqrt(sum(v * v for v in c1.values()))
    mag2 = math.sqrt(sum(v * v for v in c2.values()))
    if mag1 == 0 or mag2 == 0:
        return 0.0
    return dot / (mag1 * mag2)


def compute_semantic_similarity(text_a: str, text_b: str) -> float:
    """Compute semantic cosine similarity (0.0 to 1.0) between two clinical terms or descriptions."""
    str_a = str(text_a).strip().lower()
    str_b = str(text_b).strip().lower()

    if not str_a or not str_b:
        return 0.0

    # 1. Exact match or complete substring
    if str_a == str_b:
        return 1.0
    if str_a in str_b or str_b in str_a:
        len_ratio = min(len(str_a), len(str_b)) / max(len(str_a), len(str_b))
        return round(max(0.80, len_ratio), 3)

    toks_a = set(_clean_tokens(str_a))
    toks_b = set(_clean_tokens(str_b))

    # 2. Token Jaccard overlap
    if toks_a and toks_b:
        overlap = len(toks_a & toks_b)
        if overlap > 0:
            jaccard = overlap / len(toks_a | toks_b)
            if jaccard >= 0.5:
                return round(0.75 + 0.25 * jaccard, 3)

    # 3. Clinical Synonym Cluster Matching
    synonym_score = 0.0
    for syn_set, weight in CLINICAL_SYNONYM_PAIRS:
        has_a = any(t in syn_set or any(s in t for s in syn_set) for t in toks_a) or any(s in str_a for s in syn_set)
        has_b = any(t in syn_set or any(s in t for s in syn_set) for t in toks_b) or any(s in str_b for s in syn_set)
        if has_a and has_b:
            synonym_score = max(synonym_score, weight)

    if synonym_score > 0.0:
        return round(synonym_score, 3)

    # 4. Character 3-gram Cosine Vector Similarity (captures shared Latin/Greek roots)
    ng1 = _char_ngrams(str_a, n=3)
    ng2 = _char_ngrams(str_b, n=3)
    ngram_cos = _cosine_counters(ng1, ng2)

    return round(float(ngram_cos), 3)


def compute_pillar_similarity(
    required_items: list[str], facility_items: list[str], threshold: float = 0.45
) -> tuple[float, list[str]]:
    """Compare required items against facility items using dense semantic similarity.
    
    Returns:
        (composite_similarity_score [0.0 - 1.0], list of matching facility claims)
    """
    if not required_items or not facility_items:
        return 0.0, []

    matched = []
    total_req = len(required_items)
    score_sum = 0.0

    for req in required_items:
        best_score = 0.0
        best_item = None
        for fac_item in facility_items:
            sim = compute_semantic_similarity(req, str(fac_item))
            if sim > best_score:
                best_score = sim
                best_item = fac_item

        if best_score >= threshold:
            score_sum += min(best_score, 1.0)
            if best_item and str(best_item) not in matched:
                matched.append(str(best_item))

    return round(score_sum / total_req, 3), matched
