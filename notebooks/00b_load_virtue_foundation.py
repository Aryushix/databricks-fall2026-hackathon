# Databricks notebook source
# MAGIC %md
# MAGIC # 00b · Load the Virtue Foundation dataset
# MAGIC
# MAGIC The hackathon data arrives as a **read-only shared catalog** (Catalog → Shares received → `databricks_virtue_foundation_dataset_dais_2026`).
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
# Full name of the shared catalog. "catalog.schema" also works.
dbutils.widgets.text("shared_catalog", "databricks_virtue_foundation_dataset_dais_2026", "Shared catalog (full name)")
dbutils.widgets.text("shared_schema", "virtue_foundation_dataset", "Shared schema")
shared_catalog = dbutils.widgets.get("shared_catalog").strip().strip("`")
shared_schema = dbutils.widgets.get("shared_schema").strip().strip("`")
if "." in shared_catalog:
    shared_catalog, shared_schema = [part.strip("`") for part in shared_catalog.split(".", 1)]

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {cfg.namespace} COMMENT 'Fall 2026 hackathon'")
for volume in ["raw", "docs"]:  # raw: CSV exports and extra files; docs: PDFs for the optional chatbot
    spark.sql(f"CREATE VOLUME IF NOT EXISTS {cfg.namespace}.{volume}")

if not shared_catalog:
    visible = [row[0] for row in spark.sql("SHOW CATALOGS").collect()]
    found = [name for name in visible if "virtue" in name.lower()]
    if not found:
        raise ValueError(
            "Paste the shared catalog's full name into the 'Shared catalog (full name)' widget at the top. "
            f"Catalogs this notebook can see: {visible}"
        )
    shared_catalog = found[0]

source = f"`{shared_catalog}`.`{shared_schema}`"
print(f"Copying from {source} into {cfg.namespace}")
display(spark.sql(f"SHOW TABLES IN {source}"))

# COMMAND ----------

for row in spark.sql(f"SHOW TABLES IN {source}").collect():
    name = row.tableName
    target = cfg.table(f"bronze_{name}")
    spark.sql(f"""
        CREATE OR REPLACE TABLE {target}
        TBLPROPERTIES ('delta.columnMapping.mode' = 'name')
        AS SELECT *,
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

# COMMAND ----------

# Give the team access to everything in our schema (run by the workspace admin)
dbutils.widgets.text("grant_to", "account users", "Give access to (group or email)")
principal = dbutils.widgets.get("grant_to")
try:
    spark.sql(f"GRANT USE CATALOG ON CATALOG {cfg.catalog} TO `{principal}`")
    spark.sql(f"GRANT ALL PRIVILEGES ON SCHEMA {cfg.namespace} TO `{principal}`")
    print(f"Granted access on {cfg.namespace} to {principal}")
except Exception as e:
    print(f"Couldn't grant to {principal} (only the workspace admin can): {e}")
