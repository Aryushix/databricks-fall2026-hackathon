"""Command line: python -m hackathon.verification {run,show,profile,rank} ...  (or tools/verify_treatments.py)

  run      verify every facility for the given treatments; writes records.jsonl, summary.json, run_log.jsonl
  show     query mode 1: one hospital-treatment record
  profile  query mode 2: a hospital's capability profile (every treatment not UNVERIFIED, by status then confidence)
  rank     query mode 3: hospitals for one treatment by status, confidence, then distance from a point

Data stays out of Git: point --out-dir at a folder outside the repo (e.g. ~/hackathon-data/verification).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from pathlib import Path

from . import query, runlog
from .pipeline import DEFAULT_CONFIG, Verifier, read_facilities, write_jsonl


def _compact(rec: dict) -> dict:
    loc = rec.get("hospital_location") or {}
    return {"hospital_id": rec["hospital_id"], "hospital_name": rec.get("hospital_name"),
            "treatment": rec["normalized_treatment"]["concept_id"], "status": rec["verification_status"],
            "confidence": rec.get("confidence_score"), "reason_codes": rec.get("reason_codes"),
            "requires_manual_review": rec.get("requires_manual_review"), "location_status": loc.get("location_status")}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m hackathon.verification", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="verify facilities for treatments")
    r.add_argument("--facilities", required=True, help="CSV export of silver_facilities_verified")
    r.add_argument("--crawl-db", help="crawl.sqlite from tools/facility_crawler.py (official pages)")
    r.add_argument("--treatments", nargs="+", default=None, help="KB concept ids (default: every KB entry)")
    r.add_argument("--out-dir", required=True)
    r.add_argument("--config", default=str(DEFAULT_CONFIG))
    r.add_argument("--llm", action="store_true", help="add the Databricks model pass (needs workspace auth)")
    r.add_argument("--cache", help="SQLite label cache (default: <out-dir>/labels.sqlite)")
    r.add_argument("--workers", type=int, default=1,
                   help="processes for tagging and the rules pass (output is identical to --workers 1)")
    r.add_argument("--log-format", choices=["text", "json"], default="text", help="console log format")
    s = sub.add_parser("show", help="query mode 1: print one record")
    s.add_argument("--records", required=True)
    s.add_argument("--hospital-id", required=True)
    s.add_argument("--treatment", required=True, help="concept id")
    p = sub.add_parser("profile", help="query mode 2: a hospital's capability profile")
    p.add_argument("--records", required=True)
    p.add_argument("--hospital-id", required=True)
    k = sub.add_parser("rank", help="query mode 3: hospitals for a treatment, optionally near a point")
    k.add_argument("--records", required=True)
    k.add_argument("--treatment", required=True, help="concept id")
    k.add_argument("--lat", type=float)
    k.add_argument("--lon", type=float)
    k.add_argument("--radius-km", type=float)
    k.add_argument("--statuses", nargs="+", default=["CONFIRMED", "LIKELY"])
    k.add_argument("--limit", type=int, default=20)
    args = ap.parse_args(argv)

    if args.cmd == "show":
        rec = query.mode1(query.iter_records(args.records), args.hospital_id, args.treatment)
        if rec is None:
            print("not found", file=sys.stderr)
            return 1
        print(json.dumps(rec, indent=2, ensure_ascii=False))
        return 0
    if args.cmd == "profile":
        rows = [_compact(r) for r in query.mode2_profile(query.iter_records(args.records), args.hospital_id)]
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0
    if args.cmd == "rank":
        if (args.lat is None) != (args.lon is None):
            ap.error("give both --lat and --lon, or neither")
        rows = query.mode3_rank(query.iter_records(args.records), args.treatment, args.lat, args.lon,
                                args.radius_km, args.statuses)[: args.limit]
        out = [{**_compact(x["record"]), "straight_line_distance_km": None if x["distance_km"] is None else round(x["distance_km"], 1),
                "location_warning": x["location_warning"]} for x in rows]
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    run_id = runlog.setup(args.log_format, out / "run_log.jsonl")
    log = logging.getLogger(__name__)
    v = Verifier(args.config, args.treatments, use_llm=args.llm, cache_path=args.cache or out / "labels.sqlite",
                 workers=args.workers)
    runlog.event(log, "run_start", run_id=run_id, treatments=",".join(v.entries), kb_version=v.kb_version,
                 config_hash=v.config_hash, workers=args.workers, llm=args.llm)
    v.build(read_facilities(args.facilities), args.crawl_db)
    statuses, reasons, review = Counter(), Counter(), Counter()

    def counted(records):
        for rec in records:
            key = rec["normalized_treatment"]["concept_id"]
            statuses[(key, rec["verification_status"])] += 1
            review[(key, rec["requires_manual_review"])] += 1
            for c in rec["reason_codes"]:
                reasons[(key, c)] += 1
            yield rec

    n = write_jsonl(counted(v.run(args.treatments)), out / "records.jsonl")
    if hasattr(v.classifier, "commit"):
        v.classifier.commit()
    summary = {
        "records": n, "kb_version": v.kb_version, "pipeline_version": v.cfg["pipeline_version"],
        "config_hash": v.config_hash, "classifier": [v.classifier.model_id, v.classifier.version],
        "status_counts": {f"{k}|{s}": c for (k, s), c in sorted(statuses.items())},
        "review_counts": {f"{k}|{s}": c for (k, s), c in sorted(review.items())},
        "reason_counts": {f"{k}|{s}": c for (k, s), c in sorted(reasons.items())},
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    runlog.event(log, "run_done", records=n, out_dir=str(out),
                 status_counts=",".join(f"{k}|{s}={c}" for (k, s), c in sorted(statuses.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
