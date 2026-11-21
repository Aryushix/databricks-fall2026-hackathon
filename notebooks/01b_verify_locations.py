# Databricks notebook source
# MAGIC %md
# MAGIC # 01b · Check every facility's location against India Post → `silver_facilities_verified`
# MAGIC
# MAGIC Run after `01_clean_facilities`. Uses the challenge's own `india_post_pincode_directory` table.
# MAGIC
# MAGIC Each facility has three location signals: its **PIN**, its **city** (with the state) and its
# MAGIC **coordinates**. Each one is looked up in the India Post directory and compared with the others:
# MAGIC two signals agree when they point to places within 25 km of each other.
# MAGIC
# MAGIC | Result | Meaning |
# MAGIC | --- | --- |
# MAGIC | `verified` | Every location field agrees |
# MAGIC | `partly_verified` | Two fields agree; the third is probably wrong, or not in India Post |
# MAGIC | `conflicting` | The fields point to different places: the location is uncertain |
# MAGIC | `unverified` | Only one field could be checked |
# MAGIC | `not_found` | Neither the PIN nor the city is in India Post, and the coordinates are not near a post office in the row's state: **removed** |
# MAGIC
# MAGIC Every row gets `location_evidence`, a sentence you can show users, and `resolved_latitude` /
# MAGIC `resolved_longitude`, the best point to measure distances from (the PIN area when the coordinates
# MAGIC are wrong). Removed rows go to `silver_facilities_location_removed` with the reason, so nothing is lost.
# MAGIC See `docs/LOCATION_CHECKS.md`.

# COMMAND ----------

# MAGIC %pip install -q phonenumbers

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import os
import sys

sys.path.append(os.path.abspath("../src"))
from hackathon import facility_silver, location_check

CATALOG = "workspace"
SCHEMA = "hackathon"
TARGET = CATALOG + "." + SCHEMA
SILVER = TARGET + ".silver_facilities"
VERIFIED = TARGET + ".silver_facilities_verified"
REMOVED = TARGET + ".silver_facilities_location_removed"
PIN_TABLES = [
    TARGET + ".bronze_india_post_pincode_directory",
    "`databricks_virtue_foundation_dataset_dais_2026`.`virtue_foundation_dataset`.`india_post_pincode_directory`",
]
# Statuses to remove. Add "conflicting" to also drop rows whose location fields disagree.
REMOVE_STATUSES = ["not_found"]

# COMMAND ----------

# 1. Load the India Post directory
pin_table = next((t for t in PIN_TABLES if spark.catalog.tableExists(t.replace("`", ""))), None)
if pin_table is None:
    raise ValueError("No India Post table found. Run 00b_load_virtue_foundation first. Looked for: "
                     + ", ".join(PIN_TABLES))
raw_directory = spark.table(pin_table).toPandas()
print("Directory:", pin_table, "-", len(raw_directory), "rows; columns:", list(raw_directory.columns))
directory = location_check.Directory(location_check.normalize_directory(raw_directory))
print(len(directory.pin_states), "PINs;", "with" if directory.has_coords else "WITHOUT", "office coordinates")

# COMMAND ----------

# 2. Check every facility
silver = spark.table(SILVER)
needed = ["unique_id", "address_zipOrPostcode", "address_city", "address_stateOrRegion", "latitude",
          "longitude", "address_line1", "address_line2", "address_line3", "dq_flags"]
facilities = silver.select(needed).toPandas()
results, summary = location_check.verify(facilities, directory, remove_statuses=REMOVE_STATUSES)
for key, value in summary.items():
    print(key, ":", value)

# COMMAND ----------

# 3. Write the kept and removed facilities
checked = silver.drop("dq_flags", "dq_flag_count").join(
    facility_silver.location_results_to_spark(spark, results), "unique_id", "left")
kept = checked.filter("keep").drop("keep")
removed = checked.filter("NOT keep").drop("keep")
kept.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(VERIFIED)
removed.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(REMOVED)
facility_silver.add_comments(spark, VERIFIED)
facility_silver.add_location_comments(spark, VERIFIED)
spark.sql("COMMENT ON TABLE " + VERIFIED + " IS 'Facilities whose location was checked against India Post. "
          "Use resolved_latitude/resolved_longitude for distances and quote location_evidence.'")
print(VERIFIED, "-", spark.table(VERIFIED).count(), "facilities")
print(REMOVED, "-", spark.table(REMOVED).count(), "facilities removed")

# COMMAND ----------

# 4. How many facilities got each result
display(spark.sql(
    "SELECT location_status, location_confidence, count(*) AS facilities FROM " + VERIFIED
    + " GROUP BY ALL ORDER BY facilities DESC"))

# COMMAND ----------

# 5. Examples of fields that are probably wrong
display(spark.sql(
    "SELECT name, address_city, address_zipOrPostcode, pin_check, city_check, coords_check, state_check, "
    "location_evidence FROM " + VERIFIED
    + " WHERE 'contradicted' IN (pin_check, city_check, coords_check, state_check) LIMIT 50"))

# COMMAND ----------

# MAGIC %md
# MAGIC ### Next
# MAGIC - Build the recommender on `silver_facilities_verified`, not `silver_facilities`.
# MAGIC - Measure distance from `resolved_latitude` / `resolved_longitude`, and show `location_confidence`
# MAGIC   next to each result; quote `location_evidence` when you explain a recommendation.
# MAGIC - When `facilities_enriched.csv` arrives: run `01_clean_facilities`, then this notebook again.
