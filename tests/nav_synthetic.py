"""SYNTHETIC facilities and verification records for navigation tests. Invented names and ids only."""

import pandas as pd

from hackathon.navigation.store import FileCapabilityStore

P = (19.0760, 72.8777)            # the synthetic patient's point
KM_PER_DEG = 111.195


def at(km_north: float, km_east: float = 0.0):
    import math
    return (P[0] + km_north / KM_PER_DEG, P[1] + km_east / (KM_PER_DEG * math.cos(math.radians(P[0]))))


def facility(uid, km, ftype="hospital", specialties=(), location_status="verified", dup=None, name=None,
             source_urls=("https://example.org/listing",), coords=True):
    lat, lon = at(km) if coords else (None, None)
    return {"unique_id": uid, "name": name or f"Synthetic Facility {uid}", "facilityTypeId": ftype,
            "specialties": str(list(specialties)).replace("'", '"'), "address_city": "Synthville",
            "address_stateOrRegion": "Synth State", "source_urls": str(list(source_urls)).replace("'", '"'),
            "dup_group_id": dup, "resolved_latitude": None if lat is None else str(lat),
            "resolved_longitude": None if lon is None else str(lon), "location_status": location_status,
            "location_confidence": "high"}


def record(uid, kb, status, conf, km, location_status="verified", review=False, reasons=(), codes=("DIRECT_SINGLE_ORIGIN",),
           quotes=(("Synthetic unit offers this service around the clock.", "capability", "extracted_record", None),),
           kb_review_status="draft"):
    lat, lon = at(km)
    return {"hospital_id": uid,
            "normalized_treatment": {"concept_id": kb, "name": kb.replace("_", " ").title(), "kb_version": f"0.2.0#{kb[:6]}",
                                     "kb_review_status": kb_review_status},
            "verification_status": status, "reason_codes": list(codes), "confidence_score": conf,
            "confidence_basis": "heuristic_v1", "requires_manual_review": review, "review_reasons": list(reasons),
            "hospital_location": {"resolved_latitude": lat, "resolved_longitude": lon, "location_status": location_status},
            "direct_evidence": [{"quote": q, "field": f, "source_type": t, "source_url": u} for q, f, t, u in quotes],
            "pipeline_version": "0.2.0-phase2", "config_hash": "synthetic00", "run_timestamp": "2026-11-21T00:00:00+00:00"}


def store(facilities, records):
    return FileCapabilityStore(pd.DataFrame(facilities), records)
