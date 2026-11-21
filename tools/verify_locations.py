#!/usr/bin/env python3
"""Check every facility's location against the India Post PIN directory, on a laptop.

The official run is notebooks/01b_verify_locations.py on Databricks, which uses the shared
india_post_pincode_directory table. This script runs the same checks (src/hackathon/location_check.py)
on a CSV, using either a CSV copy of the directory or the copy bundled with the `indiapins` package.

    pip install -r tools/requirements.txt

    python tools/verify_locations.py --facilities facilities_cleaned.csv \
        --out facilities_verified.csv --removed facilities_location_removed.csv

    --directory pincodes.csv          use your own copy of the directory instead of `indiapins`
    --also-remove conflicting         also drop rows whose location fields point to different places
    --issues location_issues.csv      write a short review list of every row with a location problem

Data stays out of Git: write the outputs to a folder outside the repo (e.g. ~/hackathon-data).
"""
import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from hackathon import location_check  # noqa: E402

ISSUE_COLUMNS = ["unique_id", "name", "address_line1", "address_city", "address_stateOrRegion",
                 "address_zipOrPostcode", "latitude", "longitude", "location_status",
                 "location_confidence", "pin_check", "city_check", "coords_check", "state_check",
                 "location_evidence"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--facilities", required=True, help="cleaned (or enriched) facilities CSV")
    ap.add_argument("--directory", default="indiapins", help="India Post directory CSV, or 'indiapins'")
    ap.add_argument("--out", required=True, help="facilities kept, with the location columns")
    ap.add_argument("--removed", help="facilities removed, with the reason")
    ap.add_argument("--issues", help="review list: only rows with a location problem")
    ap.add_argument("--also-remove", nargs="*", default=[], choices=["conflicting", "unverified"])
    ap.add_argument("--agree-km", type=float, default=location_check.AGREE_KM)
    args = ap.parse_args()

    fac = pd.read_csv(args.facilities, dtype=str, keep_default_na=False, low_memory=False)
    fac = fac.replace({"": None})
    raw = (location_check.load_indiapins() if args.directory == "indiapins"
           else pd.read_csv(args.directory, dtype=str, low_memory=False))
    directory = location_check.Directory(location_check.normalize_directory(raw))
    results, summary = location_check.verify(fac, directory, args.agree_km,
                                             remove_statuses=("not_found", *args.also_remove))
    kept, removed = location_check.apply(fac, results)
    kept.to_csv(args.out, index=False)
    print(f"wrote {len(kept)} facilities to {args.out}")
    if args.removed:
        removed.to_csv(args.removed, index=False)
        print(f"wrote {len(removed)} removed facilities to {args.removed}")
    if args.issues:
        problem = (kept.location_status != "verified") | (kept.state_check == "contradicted")
        issues = pd.concat([kept[problem], removed])[ISSUE_COLUMNS]
        issues.to_csv(args.issues, index=False)
        print(f"wrote {len(issues)} rows with a location problem to {args.issues}")
    print(json.dumps(summary, indent=1, default=str))


if __name__ == "__main__":
    main()
