"""Command line: python -m hackathon.verification {run,show} ...  (or tools/verify_treatments.py)

  run   verify every facility for the given treatments and write records.jsonl + summary.json
  show  print one hospital-treatment record from a records.jsonl

Data stays out of Git: point --out-dir at a folder outside the repo (e.g. ~/hackathon-data/verification).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from pathlib import Path

from .pipeline import DEFAULT_CONFIG, Verifier, read_facilities, write_jsonl


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
    s = sub.add_parser("show", help="print one record")
    s.add_argument("--records", required=True)
    s.add_argument("--hospital-id", required=True)
    s.add_argument("--treatment", required=True, help="concept id")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    if args.cmd == "show":
        with open(args.records, encoding="utf-8") as fh:
            for line in fh:
                rec = json.loads(line)
                if rec["hospital_id"] == args.hospital_id and rec["normalized_treatment"]["concept_id"] == args.treatment:
                    print(json.dumps(rec, indent=2, ensure_ascii=False))
                    return 0
        print("not found", file=sys.stderr)
        return 1

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    v = Verifier(args.config, args.treatments, use_llm=args.llm, cache_path=args.cache or out / "labels.sqlite")
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
    logging.getLogger(__name__).info("wrote %d records to %s", n, out / "records.jsonl")
    return 0


if __name__ == "__main__":
    sys.exit(main())
