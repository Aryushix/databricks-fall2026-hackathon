"""Copied listings: claims that recur under unrelated facilities are a warning, never extra support.

Two detectors, both over extracted claims only:
  * text copies  - a long claim (6+ words) whose text, minus the facility's own name and any numbers,
                   recurs (exactly, or as a MinHash near-duplicate) under 3+ unrelated facilities.
  * block copies - a run of list items pasted into several unrelated facilities' lists: a facility
                   shares 6+ items, making up 20%+ of its list, with each of 2+ unrelated facilities.
"Unrelated" means the facilities' names share no distinctive word, so branches of one chain
("Apollo Hospitals Chennai", "Apollo Hospitals Hyderabad") never count as copies of each other.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations

import numpy as np

from .entities import FacilityProfile
from .records import Claim

_NUM = re.compile(r"\d+")
_WORDS = re.compile(r"[a-z]+")


@dataclass(frozen=True)
class CopyInfo:
    kind: str           # text | block
    cluster_id: str
    facilities: int     # unrelated facilities carrying it


class _UnionFind:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def _strip(text: str, profile: FacilityProfile) -> list[str]:
    words = _WORDS.findall(_NUM.sub(" ", text.lower()))
    return [w for w in words if w not in profile.distinctive]


def _brands(uids, profiles) -> int:
    """Number of unrelated name groups among these facilities (shared distinctive word = same group)."""
    uf = _UnionFind()
    owner = {}
    for u in uids:
        uf.find(u)
        for t in profiles[u].distinctive:
            if t in owner:
                uf.union(u, owner[t])
            else:
                owner[t] = u
    return len({uf.find(u) for u in uids})


_PRIME = (1 << 61) - 1
_COEFFS: dict[int, tuple[list[int], list[int]]] = {}


def _minhash(shingles: set[str], perms: int) -> tuple[int, ...]:
    """MinHash signature: each shingle hashed once, then `perms` universal hash functions (a*x + b mod p)."""
    if perms not in _COEFFS:
        rng = np.random.default_rng(20261121)      # fixed seed: signatures are stable across runs
        _COEFFS[perms] = ([int(v) for v in rng.integers(1, _PRIME, perms, dtype=np.uint64)],
                          [int(v) for v in rng.integers(0, _PRIME, perms, dtype=np.uint64)])
    a, b = _COEFFS[perms]
    xs = [int.from_bytes(hashlib.blake2b(s.encode(), digest_size=8).digest(), "big") for s in shingles]
    return tuple(min((ai * x + bi) % _PRIME for x in xs) for ai, bi in zip(a, b))


def text_copies(claims: list[Claim], profiles: dict[str, FacilityProfile], min_words: int = 6,
                min_facilities: int = 3, perms: int = 64, bands: int = 16, jaccard: float = 0.8) -> dict[str, CopyInfo]:
    """claim_id -> CopyInfo for claims whose text recurs under min_facilities+ unrelated facilities."""
    items = {}
    for c in claims:
        if c.lineage != "vf_extraction" or c.field == "specialties":
            continue
        words = _strip(c.text, profiles[c.unique_id])
        if len(words) >= min_words:
            items[c.claim_id] = (c.unique_id, words)
    uf = _UnionFind()
    by_exact = defaultdict(list)
    for cid, (_, words) in items.items():
        by_exact[" ".join(words)].append(cid)
    for group in by_exact.values():
        for other in group[1:]:
            uf.union(group[0], other)
    # near duplicates: one representative per exact group, MinHash LSH, then a real Jaccard check
    reps = {group[0]: set(zip(items[group[0]][1], items[group[0]][1][1:], items[group[0]][1][2:]))
            for group in by_exact.values()}
    rows = perms // bands
    buckets = defaultdict(list)
    sigs = {}
    for cid, sh in reps.items():
        sh = {" ".join(s) for s in sh}
        if not sh:
            continue
        sigs[cid] = (sh, _minhash(sh, perms))
        for b in range(bands):
            buckets[(b, sigs[cid][1][b * rows:(b + 1) * rows])].append(cid)
    for members in buckets.values():
        if 1 < len(members) <= 200:
            for a, b in combinations(members, 2):
                sa, sb = sigs[a][0], sigs[b][0]
                if len(sa & sb) / len(sa | sb) >= jaccard:
                    uf.union(a, b)
    clusters = defaultdict(list)
    for cid in items:
        clusters[uf.find(cid)].append(cid)
    out = {}
    for root, members in clusters.items():
        uids = {items[m][0] for m in members}
        if len(uids) < min_facilities:
            continue
        n = _brands(uids, profiles)
        if n >= min_facilities:
            info = CopyInfo("text", f"text:{root}", n)
            for m in members:
                out[m] = info
    return out


def block_copies(claims: list[Claim], profiles: dict[str, FacilityProfile], min_shared: int = 6,
                 min_share: float = 0.25, min_facilities: int = 3, max_df: int = 60) -> dict[str, CopyInfo]:
    """claim_id -> CopyInfo for list items pasted as a block into min_facilities+ unrelated facilities."""
    lists = defaultdict(dict)                       # (uid, field) -> normalized item -> claim_id
    for c in claims:
        if c.lineage == "vf_extraction" and c.field in ("capability", "procedure", "equipment"):
            key = " ".join(_WORDS.findall(c.text.lower()))
            if len(key.split()) >= 2:
                lists[(c.unique_id, c.field)][key] = c.claim_id
    inv = defaultdict(set)
    for (uid, fld), items in lists.items():
        for k in items:
            inv[(fld, k)].add(uid)
    shared = defaultdict(lambda: defaultdict(set))  # (uid, field) -> partner -> shared items
    for (fld, k), uids in inv.items():
        if 2 <= len(uids) <= max_df:
            for a, b in combinations(sorted(uids), 2):
                shared[(a, fld)][b].add(k)
                shared[(b, fld)][a].add(k)
    out = {}
    for (uid, fld), partners in shared.items():
        size = len(lists[(uid, fld)])
        good = {p: ks for p, ks in partners.items()
                if len(ks) >= min_shared and len(ks) / size >= min_share
                and not (profiles[uid].distinctive & profiles[p].distinctive)}
        if len(good) < min_facilities - 1:
            continue
        item_partners = defaultdict(set)
        for p, ks in good.items():
            for k in ks:
                item_partners[k].add(p)
        for k, ps in item_partners.items():
            if len(ps) >= min_facilities - 1:
                n = _brands({uid, *ps}, profiles)
                if n >= min_facilities:
                    out[lists[(uid, fld)][k]] = CopyInfo("block", f"block:{fld}:{k[:60]}", n)
    return out
