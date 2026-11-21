# Databricks notebook source
# MAGIC %md
# MAGIC # 01 · Clean the facilities → `silver_facilities`
# MAGIC
# MAGIC Applies the team's cleaning rules (`src/hackathon/cleaning.py`, the same code as `tools/clean_dataset.py`)
# MAGIC and writes one typed row per facility, with a `dq_flags` column listing every issue found or fix applied.
# MAGIC See `docs/DATA_CLEANING.md` for what each rule and flag means.
# MAGIC
# MAGIC **Where the data comes from** (first one found wins; files go in the `raw` volume, never in Git):
# MAGIC
# MAGIC | Put this in `/Volumes/workspace/hackathon/raw/` | What happens |
# MAGIC | --- | --- |
# MAGIC | `facilities_enriched.csv` | Already cleaned and crawled on a laptop: loaded as-is |
# MAGIC | `facilities_cleaned.csv` | Already cleaned on a laptop: loaded as-is |
# MAGIC | `dataset.csv` | Raw export: cleaned here, identical to the laptop script's output |
# MAGIC | nothing | Uses `bronze_facilities` from `00b`. 30 records that were split across lines in the source lose their last 23 fields; they are kept and flagged `row_truncated_in_source` |

# COMMAND ----------

# MAGIC %pip install -q phonenumbers

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import os
import sys

sys.path.append(os.path.abspath("../src"))
from hackathon import cleaning, facility_silver

CATALOG = "workspace"
SCHEMA = "hackathon"
TARGET = CATALOG + "." + SCHEMA
RAW_DIR = "/Volumes/" + CATALOG + "/" + SCHEMA + "/raw"
SILVER = TARGET + ".silver_facilities"

# COMMAND ----------

# 1. Load the best available source
frame, repaired_ids, extra_flags, already_clean, source = facility_silver.load_source(
    spark, RAW_DIR, TARGET + ".bronze_facilities"
)
print("Source:", source)
print("Rows loaded:", len(frame))

# COMMAND ----------

# 2. Clean (skipped when the file was already cleaned on a laptop)
if already_clean:
    cleaned = frame
    print("Already cleaned; no rules applied.")
else:
    cleaned, stats = cleaning.clean(frame, repaired_ids, extra_flags)
    print("Rows in:", stats["raw_rows"], "| exact duplicates dropped:", stats["exact_duplicates_dropped"],
          "| rows out:", stats["rows_out"])
    print("Columns dropped:", ", ".join(cleaning.DROP_COLS))

# COMMAND ----------

# 3. Write silver_facilities (lists become array<string>, numbers and dates get real types)
silver = facility_silver.to_silver(spark, cleaned)
silver.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(SILVER)
facility_silver.add_comments(spark, SILVER)
print(SILVER, "-", spark.table(SILVER).count(), "facilities")

# COMMAND ----------

# 4. Data-quality summary: how many facilities carry each flag
summary = facility_silver.dq_summary(spark.table(SILVER))
summary.write.mode("overwrite").saveAsTable(SILVER + "_dq_summary")
display(summary)

# COMMAND ----------

# MAGIC %md
# MAGIC ### Next
# MAGIC - `02_clean_silver` for the other two tables (`india_post_pincode_directory`, the NFHS-5 table).
# MAGIC - Treat every field as a claim. Before recommending a facility, check its `dq_flags`
# MAGIC   (e.g. `possible_duplicate`, `coords_far_from_city`, `row_truncated_in_source`) and cite its `description` and `source_urls`.
