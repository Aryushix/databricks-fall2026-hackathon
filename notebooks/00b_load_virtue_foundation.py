# Databricks notebook source
# MAGIC %md
# MAGIC # 00b · Load the Virtue Foundation dataset
# MAGIC
# MAGIC The hackathon data arrives as a **read-only shared catalog** (Catalog → Shares received → `databricks_virtue_foundation_...`).
# MAGIC This notebook copies each shared table into our writable schema as `bronze_<table>`, so the rest of the repo
# MAGIC (`02_clean_silver` onward) can use it.
# MAGIC
# MAGIC **The data never goes into GitHub.** This notebook is the link between the repo and the dataset. Rerun it any time to refresh the copies.
# MAGIC
# MAGIC | Shared table | Copied to |
# MAGIC | --- | --- |
# MAGIC | `facilities` | `bronze_facilities` |
# MAGIC | `india_post_pincode_directory` | `bronze_india_post_pincode_directory` |
# MAGIC | `nfhs_5_district_health_...` | `bronze_nfhs_5_district_health_...` |

# COMMAND ----------

import os
import sys

sys.path.append(os.path.abspath("../src"))
from hackathon import from_widgets

cfg = from_widgets(dbutils)
dbutils.widgets.text("shared_catalog_prefix", "databricks_virtue_foundation", "Shared catalog name starts with")
dbutils.widgets.text("shared_schema", "virtue_foundation_dataset", "Shared schema")
prefix = dbutils.widgets.get("shared_catalog_prefix")
shared_schema = dbutils.widgets.get("shared_schema")

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {cfg.namespace} COMMENT 'Fall 2026 hackathon'")
for volume in ["raw", "docs"]:  # raw: CSV exports and extra files · docs: PDFs for the optional chatbot
    spark.sql(f"CREATE VOLUME IF NOT EXISTS {cfg.namespace}.{volume}")

matches = [row[0] for row in spark.sql("SHOW CATALOGS").collect() if row[0].startswith(prefix)]
if not matches:
    raise ValueError(
        f"No catalog starting with '{prefix}'. Open Catalog → Shares received, copy the catalog name, "
        "and put its first part in the 'Shared catalog name starts with' widget."
    )
shared_catalog = matches[0]
source = f"`{shared_catalog}`.`{shared_schema}`"
print(f"Copying from {source} into {cfg.namespace}")

# COMMAND ----------

for row in spark.sql(f"SHOW TABLES IN {source}").collect():
    name = row.tableName
    target = cfg.table(f"bronze_{name}")
    spark.sql(f"""
        CREATE OR REPLACE TABLE {target} AS
        SELECT *,
               '{shared_catalog}.{shared_schema}.{name}' AS _source_file,
               current_timestamp()                       AS _ingested_at
        FROM {source}.`{name}`
    """)
    spark.sql(f"COMMENT ON TABLE {target} IS 'Bronze copy of the Virtue Foundation shared table {name}'")
    copied = spark.table(target)
    print(f"{target}: {copied.count():,} rows, {len(copied.columns)} columns")

# COMMAND ----------

# MAGIC %md ### What we have now
# MAGIC Next: run `02_clean_silver` with `dataset` set to `facilities` (then the other two tables).

# COMMAND ----------

for table in spark.catalog.listTables(cfg.namespace):
    if table.name.startswith("bronze_"):
        print(f"\n=== {table.name}")
        spark.table(cfg.table(table.name)).printSchema()
