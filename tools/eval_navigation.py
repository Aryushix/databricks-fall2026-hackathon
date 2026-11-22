#!/usr/bin/env python3
"""Evaluate symptom navigation. Metrics measure consistency with the verifier, not clinical correctness.

    # extraction, triage and care needs only (synthetic cases, no data needed)
    python tools/eval_navigation.py --out-dir ~/hackathon-data/navigation-eval

    # plus capability consistency, coverage across sampled Indian locations, and the human-judgment CSV
    python tools/eval_navigation.py --data-dir ~/hackathon-data --out-dir ~/hackathon-data/navigation-eval

    --llm   use the Databricks model for extraction (needs workspace auth); default is the rule extractor

Writes metrics.json, report.md and (with data) human_judgment.csv to --out-dir, which must be outside
the repo: the judgment CSV contains real facility names.
"""

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hackathon.navigation import evaluate as ev  # noqa: E402
from hackathon.navigation.red_flags import REPO_ROOT  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", default=str(ev.DEFAULT_CASES))
    ap.add_argument("--data-dir", help="folder with facilities_enriched.csv, facilities_verified.csv, verification/")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--llm", action="store_true")
    ap.add_argument("--locations-per-state", type=int, default=2)
    ap.add_argument("--judgment-cases", type=int, default=25)
    args = ap.parse_args(argv)

    out = Path(args.out_dir).expanduser().resolve()
    if out == REPO_ROOT or REPO_ROOT in out.parents:
        print("--out-dir must be outside the repo (the judgment CSV holds real facility names)", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    cases = ev.load_cases(args.cases)
    paths = None
    if args.data_dir:
        d = Path(args.data_dir).expanduser()
        rec = d / "verification" / "records.jsonl.gz"
        paths = {"enriched": str(d / "facilities_enriched.csv"), "verified": str(d / "facilities_verified.csv"),
                 "records": str(rec if rec.exists() else d / "verification" / "records.jsonl"),
                 "summary": str(d / "verification" / "summary.json")}
    nav = ev.build_components(use_llm=args.llm, data_paths=paths, with_data=bool(paths))
    metrics = {"extractor": "llm+rules" if args.llm else "rules", "cases": len(cases),
               "extraction": ev.extraction_accuracy(cases, nav.extractor),
               "red_flags": ev.red_flag_set(nav.rules),
               "triage_and_needs": ev.triage_and_needs(cases, nav)}
    if paths:
        locs = ev.sample_locations(args.locations_per_state)
        cap, responses = ev.capability_and_coverage(cases, nav, locs)
        metrics["capability"] = cap
        metrics["locations"] = len(locs)
        rows = ev.judgment_rows(responses, args.judgment_cases)
        if rows:
            with open(out / "human_judgment.csv", "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "report.md").write_text(ev.report_markdown(metrics), encoding="utf-8")
    print(ev.report_markdown(metrics))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
