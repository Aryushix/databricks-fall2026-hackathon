# Data does not go in Git

All data lives in Databricks (Unity Catalog), not in this repo. `.gitignore` blocks data files, and everything in this folder except this README.

## Where the data is

| What | Where |
| --- | --- |
| Original Virtue Foundation data (read-only) | **Catalog → Shares received → `databricks_virtue_foundation_dataset_dais_2026` → `virtue_foundation_dataset`** |
| Our copies (`bronze_*`), cleaned tables (`silver_*`), results (`gold_*`) | `workspace.hackathon` |
| CSV exports and any extra files | Volume `/Volumes/workspace/hackathon/raw/` |
| PDFs for the optional chatbot | Volume `/Volumes/workspace/hackathon/docs/` |

## Sources

| Dataset | Tables | Where it came from | Terms |
| --- | --- | --- | --- |
| Virtue Foundation hackathon dataset | `facilities`, `india_post_pincode_directory`, `nfhs_5_district_health_...` | Shared with the workspace through OpenSharing (Catalog → Shares received) | Provider's sharing terms: keep it out of public repos |
| TODO: anything else you add | | | |
