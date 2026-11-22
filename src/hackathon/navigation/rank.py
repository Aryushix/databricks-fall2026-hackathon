"""Capability filter, distance and ranking: an extension of query mode 3 (verification/query.py).

For each required care need that can be assessed (its KB, or a proxy KB, has records in the loaded
verification run), mode 3 gives every facility's status, confidence and distance. A facility enters the
verified list when at least one assessable required need is CONFIRMED or LIKELY and none is
CONTRADICTED. Tiered sort, not a weighted sum, so proximity can never outvote missing capability:

  1. status tier of the weakest assessable required need (CONFIRMED 1, LIKELY 2, unmatched 3)
  2. number of assessable required needs matched (more first)
  3. confidence band of the weakest matched need (bands from config, per urgency)
  4. straight-line distance

The recommender reads only status, confidence, reason codes, review flags and quotes from verifier
records. It never re-scores evidence or reads claim text to decide capability.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from ..cleaning import haversine_km
from ..verification.query import mode3_rank
from .care_needs import CareNeedTable
from .contract import CareNeed, EvidenceQuote, MatchedNeed, Recommendation, SearchInfo, UnverifiedSignal
from .travel import NullTravelEstimator, TravelEstimator

LOCATION_REVIEW = ("partly_verified", "conflicting", "unverified")
BAND_LABELS = {2: ["typical", "stronger"], 3: ["weaker", "typical", "stronger"]}


def _group(fac, hid) -> Optional[str]:
    """The facility's duplicate group, or None. (pandas stores "no group" as NaN, which is truthy.)"""
    g = fac.at[hid, "dup_group_id"]
    return g if isinstance(g, str) and g else None


def _band(conf: Optional[float], edges: list[float]) -> int:
    return sum(1 for e in edges if (conf or 0.0) >= e)


def _kb_name(kb: str, rec: dict) -> str:
    return ((rec.get("normalized_treatment") or {}).get("name") or kb.replace("_", " ")).lower()


class NavigationRanker:
    def __init__(self, cfg: dict, table: CareNeedTable, travel: Optional[TravelEstimator] = None):
        self.cfg, self.table = cfg, table
        self.travel = travel or NullTravelEstimator()
        sp = cfg["status_policy"]
        self.eligible, self.tier, self.never = set(sp["eligible"]), sp["tier"], set(sp["never_show"])
        self.w = cfg["warnings"]

    # ------------------------------------------------------------------ evidence per need
    def _evidence_kbs(self, need: CareNeed, available: set[str]) -> tuple[list[str], str]:
        if need.verification_kb and need.verification_kb in available:
            return [need.verification_kb], "kb"
        proxies = [k for k in need.proxy_kbs if k in available]
        return (proxies, "proxy_kb") if proxies else ([], "none")

    def _status_by_facility(self, store, kbs: list[str], lat: float, lon: float, radius: float) -> dict:
        """hospital_id -> best mode-3 row across the need's KBs (proxy KBs: best of them)."""
        best: dict = {}
        for kb in kbs:
            for row in mode3_rank(store.records_for(kb), kb, lat, lon, radius):
                row = {**row, "kb": kb}
                cur = best.get(row["hospital_id"])
                if cur is None or (self.tier.get(row["status"], 9), -(row["confidence"] or 0)) < \
                        (self.tier.get(cur["status"], 9), -(cur["confidence"] or 0)):
                    best[row["hospital_id"]] = row
        return best

    # ------------------------------------------------------------------ main entry
    def rank(self, store, needs: list[CareNeed], urgency: str, lat: float, lon: float, max_results: int):
        sc = self.cfg["search"]
        u = urgency if urgency in sc["radius_km"] else "routine"
        radius, widen = float(sc["radius_km"][u]), float(sc["widen_to_km"][u])
        edges = list(sc["band_edges"][u])
        fac = store.facilities()
        available = store.kb_ids()
        dist_all = haversine_km(lat, lon, fac.latitude.to_numpy(float), fac.longitude.to_numpy(float))
        has_xy = ~np.isnan(dist_all)
        n_no_xy = int((~has_xy).sum())

        required = [n for n in needs if n.role == "required"]
        assess = {}                                   # need id -> (kbs, via)
        for n in needs:
            kbs, via = self._evidence_kbs(n, available)
            if kbs:
                assess[n.id] = (kbs, via)
        req_assess = [n for n in required if n.id in assess]
        # statuses for every facility (no radius): the emergency nearest list can reach beyond the search area
        rows = {n.id: self._status_by_facility(store, assess[n.id][0], lat, lon, None) for n in needs if n.id in assess}

        contradicted = {hid for n in required if n.id in rows for hid, r in rows[n.id].items() if r["status"] in self.never}

        def verified_within(r_km: float) -> list[tuple]:
            out = []
            for i, hid in enumerate(fac.index):
                d = dist_all[i]
                if not has_xy[i] or d > r_km or hid in contradicted or not req_assess:
                    continue
                st = {n.id: rows[n.id].get(hid) for n in req_assess}
                matched = [nid for nid, r in st.items() if r and r["status"] in self.eligible]
                if not matched:
                    continue
                tiers = [self.tier[st[nid]["status"]] if nid in matched else 3 for nid in st]
                weakest_conf = min(st[nid]["confidence"] or 0.0 for nid in matched)
                band = _band(weakest_conf, edges)
                out.append(((max(tiers), -len(matched), -band, float(d)), hid, band))
            return sorted(out)

        widened, widened_for = False, None          # widened_for: "verified" or "nearby", for the note
        ver = verified_within(radius)
        used_radius = radius
        if not ver and req_assess:
            ver, widened, used_radius, widened_for = verified_within(widen), True, widen, "verified"

        # ---------------------------------------------------------------- duplicates and truncation
        def collapse(items, seen_groups: set, seen_ids: set):
            out = []
            for it in items:
                hid = it[1]
                g = _group(fac, hid)
                if hid in seen_ids or (g and g in seen_groups):
                    continue
                seen_ids.add(hid)
                if g:
                    seen_groups.add(g)
                out.append(it)
            return out

        # ---------------------------------------------------------------- nearby facilities by type
        types = []
        for n in required or needs:
            types += [t for t in self.table.fallback_types(n.id) if t not in types]
        types = types or ["hospital"]
        pref = {t: k for k, t in enumerate(types)}
        type_rank = fac.facilityTypeId.map(pref).to_numpy(dtype=float)       # NaN: not one of these types

        def nearest(r_km: float):
            # facility types in config order of preference (dentists before hospitals for dentistry), then distance
            ok = has_xy & (dist_all <= r_km) & ~np.isnan(type_rank)
            idx = np.flatnonzero(ok)
            idx = idx[np.lexsort((dist_all[idx], type_rank[idx]))]
            return [((0, 0, 0, float(dist_all[i])), fac.index[i], 0) for i in idx if fac.index[i] not in contradicted]

        def band_of(hid) -> int:
            confs = [rows[n.id][hid]["confidence"] or 0.0 for n in req_assess
                     if hid in rows[n.id] and rows[n.id][hid]["status"] in self.eligible]
            return _band(min(confs), edges) if confs else 0

        n_fb = int(sc["fallback"]["max"])
        always = urgency == "emergency" and sc.get("emergency_always_result", False)
        beyond = None                     # km to the nearest one when it's outside the widened radius

        def near_list(r0: float, seen_groups: set, seen_ids: set):
            nonlocal widened, used_radius, beyond, widened_for
            got = collapse(nearest(r0), set(seen_groups), set(seen_ids))
            if not got and r0 < widen:
                got = collapse(nearest(widen), set(seen_groups), set(seen_ids))
                if got:
                    widened, used_radius = True, max(used_radius, widen)
                    widened_for = widened_for or "nearby"
            if not got and always:
                got = collapse(nearest(float(sc.get("emergency_always_result_max_km", 300))), set(seen_groups), set(seen_ids))
                if got:
                    beyond = got[0][0][3]
            return got[:n_fb]

        if urgency == "emergency" and sc.get("emergency_nearest_first", False):
            # Emergencies: the closest hospitals first, with any verified match they have; then verified
            # matches further away. Getting the patient a result comes first.
            near = [(k, hid, band_of(hid)) for k, hid, _ in near_list(radius, set(), set())]
            near_ids = {hid for _, hid, _ in near}
            near_groups = {g for g in (_group(fac, hid) for hid in near_ids) if g}
            ver = collapse(ver, set(near_groups), set(near_ids))[:max_results]
            sections = [("nearest", near), ("verified", ver)]
        else:
            groups, ids = set(), set()
            ver = collapse(ver, groups, ids)[:max_results]
            fallback = []
            if urgency in sc["fallback"]["always_for"] or len(ver) < max_results:
                fallback = near_list(used_radius, groups, ids)
            sections = [("verified", ver), ("unverified_fallback", fallback)]

        # ---------------------------------------------------------------- build the records
        recs = []
        for kind, items in sections:
            for key, hid, band in items:
                recs.append(self._build(kind, len(recs) + 1, hid, fac, key[3], needs, required, req_assess, assess,
                                        rows, band, edges, lat, lon))

        nearest_km = float(np.nanmin(dist_all)) if has_xy.any() else None
        note = self._coverage_note(needs, assess, available, widened_for, radius, widen, used_radius, recs, n_no_xy, beyond)
        return recs, SearchInfo(radius_km=used_radius, widened=widened,
                                nearest_facility_km=round(nearest_km, 1) if nearest_km is not None else None), note, store.versions()

    # ------------------------------------------------------------------ one recommendation
    def _build(self, kind, rank, hid, fac, d, needs, required, req_assess, assess, rows, band, edges, lat, lon):
        f = fac.loc[hid]
        name = lambda nid: self.table.name(nid)  # noqa: E731
        matched, warnings, review, reasons_seen = [], [], False, []
        for n in needs:
            r = rows.get(n.id, {}).get(hid)
            if not r or r["status"] not in self.eligible:
                continue
            rec = r["record"]
            ev = []
            for e in rec.get("direct_evidence") or []:
                unknown = not e.get("source_url")
                ev.append(EvidenceQuote(quote=e["quote"], field=e.get("field") or "", source_type=e.get("source_type") or "",
                                        source_url=e.get("source_url"), page_unknown=unknown,
                                        record_source_urls=list(f.source_urls_list[:3]) if unknown else []))
            via = assess[n.id][1]
            matched.append(MatchedNeed(care_need=n.id, role=n.role, kb_id=r["kb"], via=via, status=r["status"],
                                       confidence=r["confidence"], confidence_basis=rec.get("confidence_basis"),
                                       reason_codes=list(rec.get("reason_codes") or []),
                                       kb_review_status=(rec.get("normalized_treatment") or {}).get("kb_review_status"),
                                       evidence=ev))
            if r["status"] == "LIKELY":
                warnings.append(self.w["status"]["LIKELY"].format(need=name(n.id)))
            for c in rec.get("reason_codes") or []:
                if c in self.w["reason_codes"] and self.w["reason_codes"][c] not in warnings:
                    warnings.append(self.w["reason_codes"][c])
            for rr in rec.get("review_reasons") or []:
                reasons_seen.append(rr)
                for prefix, text in self.w["review_reasons"].items():
                    if rr.startswith(prefix) and text not in warnings:
                        warnings.append(text)
            if (rec.get("normalized_treatment") or {}).get("kb_review_status") == "draft":
                t = self.w["draft_kb"].format(need=name(n.id).lower())
                if t not in warnings:
                    warnings.append(t)
            review = review or bool(rec.get("requires_manual_review"))

        matched_ids = {m.care_need for m in matched}
        unmatched = [n.id for n in required if n.id not in matched_ids]
        for nid in unmatched:
            warnings.append(f"{name(nid)} not verified: call ahead.")
        req_matched = any(m.role == "required" for m in matched)
        if kind != "verified" and not req_matched and self.w["fallback"] not in warnings:
            warnings.insert(0, self.w["fallback"])
        loc_status = f.location_status
        if loc_status in LOCATION_REVIEW:
            review = True
            warnings.append(self.w["location_status"][loc_status])
        if f.resolved_location_source == "pin_area":
            warnings.append(self.w["approximate_location"])

        signals = []
        for n in needs:
            if n.id in matched_ids:
                continue
            sig = self.table.signals(n.id)
            for v in sig.get("specialties", []):
                if v in f.specialties_list:
                    signals.append(UnverifiedSignal(care_need=n.id, source="specialties", value=v))
                    break
            if f.facilityTypeId in sig.get("facility_types", []):
                signals.append(UnverifiedSignal(care_need=n.id, source="facilityTypeId", value=f.facilityTypeId))

        explanation = self._explain(kind, matched, signals, d, band, edges, f.facilityTypeId)
        phone = f.officialPhone if isinstance(f.officialPhone, str) and f.officialPhone.strip() else None
        if phone and "phone_shared_across_facilities" in str(f.dq_flags or ""):
            phone = None                                  # a call-centre or directory number, not this facility's
        travel = self.travel.estimate(lat, lon, float(f.latitude), float(f.longitude))
        return Recommendation(
            rank=rank, list=kind, hospital_id=hid, hospital_name=f["name"], facility_type=f.facilityTypeId,
            latitude=round(float(f.latitude), 6), longitude=round(float(f.longitude), 6),
            straight_line_distance_km=round(float(d), 1), travel=travel, location_status=loc_status,
            listed_phone=phone, listed_phone_note=self.w["listed_phone"] if phone else None,
            capability_match=kind in ("verified", "nearest") and req_matched, matched_care_needs=matched,
            unverified_signals=signals, unmatched_required_needs=unmatched, warnings=warnings,
            requires_manual_review=review, review_reasons=list(dict.fromkeys(reasons_seen)),
            also_listed_as=self._mates(fac, hid), explanation=explanation)

    def _mates(self, fac, hid) -> list[str]:
        g = _group(fac, hid)
        if not g:
            return []
        return [x for x in fac.index[fac.dup_group_id.to_numpy() == g] if x != hid]

    def _explain(self, kind, matched, signals, d, band, edges, ftype) -> str:
        found = []
        for m in matched:
            status = "Confirmed" if m.status == "CONFIRMED" else "Likely"
            if m.via == "proxy_kb":
                found.append(f"{status} {m.kb_id.replace('_', ' ')}, which suggests a {self.table.name(m.care_need).lower()} "
                             "department (this doesn't mean you need that procedure)")
            else:
                found.append(f"{status} {self.table.name(m.care_need).lower()}")
        labels = BAND_LABELS.get(len(edges) + 1)
        if labels and found and any(m.role == "required" for m in matched):
            found[-1] += f" ({labels[min(band, len(labels) - 1)]} evidence)"
        if kind == "verified":
            parts = found
        elif kind == "nearest":
            parts = [f"Nearest {ftype or 'facility'} in our data"] + (found or ["capability not verified, call ahead"])
        else:
            parts = [f"Nearest {ftype or 'facility'} in our data; capability not verified, call ahead"]
        if signals:
            names = list(dict.fromkeys(self.table.name(s.care_need).lower() for s in signals))
            parts.append(f"{', '.join(names)} listed but unverified")
        parts.append(f"{d:.1f} km straight-line")
        return "; ".join(parts) + "."

    def _coverage_note(self, needs, assess, available, widened_for, radius, widen, used, recs, n_no_xy, beyond=None) -> str:
        lines = []
        no_kb = [n for n in needs if n.id not in assess]
        if no_kb:
            names = [self.table.name(n.id) for n in no_kb if n.role == "required"] or [self.table.name(n.id) for n in no_kb]
            names = list(dict.fromkeys(names))
            joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
            lines.append(f"{joined} can't be verified for any facility yet.")
        if widened_for == "verified":
            lines.append(f"No verified match within {radius:.0f} km, so the search for one was widened once to {used:.0f} km.")
        elif widened_for == "nearby":
            lines.append(f"Nothing was found within {radius:.0f} km, so the search was widened once to {used:.0f} km.")
        if beyond is not None:
            lines.append(f"The nearest hospital in our data is {beyond:.0f} km away, outside the {widen:.0f} km search area.")
        elif not recs:
            limit = self.cfg["search"].get("emergency_always_result_max_km", widen) if any(
                n.id == "emergency_department" for n in needs) and self.cfg["search"].get("emergency_always_result") else widen
            lines.append(f"No facility in our data is within {max(limit, widen):.0f} km straight-line. "
                         "If this may be an emergency, call 112.")
        if n_no_xy:
            lines.append(f"{n_no_xy} facilit{'y' if n_no_xy == 1 else 'ies'} without usable coordinates "
                         f"{'was' if n_no_xy == 1 else 'were'} left out.")
        lines.append("Our data doesn't list every facility in India, and straight-line distance isn't road distance.")
        return " ".join(lines)
