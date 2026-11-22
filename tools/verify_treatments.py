#!/usr/bin/env python3
"""Verify which facilities provide a treatment, on a laptop.

    pip install -r tools/requirements.txt

    python tools/verify_treatments.py run --facilities ~/hackathon-data/facilities_verified.csv \
        --crawl-db ~/hackathon-data/crawl.sqlite --out-dir ~/hackathon-data/verification

    python tools/verify_treatments.py show --records ~/hackathon-data/verification/records.jsonl \
        --hospital-id <unique_id> --treatment cardiac_catheterization

The rules live in src/hackathon/verification (see docs/TREATMENT_VERIFICATION.md). Keep the inputs and
outputs OUT of Git: they are real facility records.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from hackathon.verification.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
