#!/usr/bin/env python3
"""Give every facility claim a claim_status and a source_trust_score (src/hackathon/verification/claim_verifier.py).

    python tools/verify_claims.py --facilities ~/hackathon-data/facilities_verified.csv \
        --crawl-db ~/hackathon-data/crawl.sqlite --out ~/hackathon-data/evidence_verified.csv.gz

Writes one row per (facility, category, claim) with claim_status, source_trust_score, corroboration_quote and
corroboration_source. Keep the output out of Git: it holds real facility text. Reading the crawl takes about
8 minutes; without --crawl-db no claim can be VERIFIED_OFFICIAL.
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from hackathon.verification.claim_verifier import ClaimVerifier, explode_claims  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--facilities", required=True, help="facilities_verified.csv (laptop copy of silver_facilities_verified)")
    ap.add_argument("--crawl-db", help="crawl.sqlite from tools/facility_crawler.py")
    ap.add_argument("--out", required=True, help="output CSV (.csv or .csv.gz)")
    args = ap.parse_args(argv)
    fac = pd.read_csv(args.facilities, dtype=str, keep_default_na=False, low_memory=False).replace({"": None})
    official = []
    if args.crawl_db:
        from hackathon.verification.ledger import official_claims
        official = official_claims(args.crawl_db, fac)
    claims = explode_claims(fac)
    res = ClaimVerifier(fac, official).verify(claims)
    res.to_csv(args.out, index=False)
    print(f"wrote {len(res):,} claims to {args.out}")
    print(res.claim_status.value_counts().to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
