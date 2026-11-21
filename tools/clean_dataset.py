#!/usr/bin/env python3
"""Clean the raw facility export on your laptop.

    pip install -r tools/requirements.txt
    python tools/clean_dataset.py dataset.csv facilities_cleaned.csv

The rules live in src/hackathon/cleaning.py, the same code notebooks/01_clean_facilities runs
on Databricks, so the laptop file and the Databricks table always match.
Keep dataset.csv and the output OUT of Git (.gitignore already blocks *.csv).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from hackathon.cleaning import main  # noqa: E402

if __name__ == "__main__":
    main(*(sys.argv[1:3] if len(sys.argv) >= 3 else ("dataset.csv", "facilities_cleaned.csv")))
