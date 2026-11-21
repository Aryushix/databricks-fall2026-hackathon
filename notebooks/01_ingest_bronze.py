# Databricks notebook source
# MAGIC %md
# MAGIC # 01 · Ingest raw files → Bronze
# MAGIC
# MAGIC Loads every new file in `raw/<dataset>/` into `bronze_<dataset>`, exactly as it arrived, plus two lineage
# MAGIC columns (`_source_file`, `_ingested_at`). Auto Loader remembers what it already loaded, so rerunning only picks up new files.
# MAGIC
# MAGIC **Don't clean anything here.** If a cleaning rule turns out wrong, Silver can be rebuilt from Bronze.

# COMMAND ----------

import os
import sys

sys.path.append(os.path.abspath("../src"))
from hackathon import from_widgets

cfg = from_widgets(dbutils)
dbutils.widgets.text("dataset", "orders", "Dataset (folder under raw/)")
dbutils.widgets.dropdown("format", "csv", ["csv", "json", "parquet"], "File format")
dataset = dbutils.widgets.get("dataset")
file_format = dbutils.widgets.get("format")

source = f"{cfg.raw_path}/{dataset}/"
checkpoint = f"{cfg.checkpoint_path}/bronze_{dataset}"
target = cfg.table(f"bronze_{dataset}")
print(f"{source}  →  {target}")

# COMMAND ----------

reader = (
    spark.readStream.format("cloudFiles")
    .option("cloudFiles.format", file_format)
    .option("cloudFiles.inferColumnTypes", "true")
    .option("cloudFiles.schemaLocation", checkpoint)  # tracks the schema over time
)
if file_format == "csv":
    reader = reader.option("header", "true")

query = (
    reader.load(source)
    .selectExpr("*", "_metadata.file_path AS _source_file", "current_timestamp() AS _ingested_at")
    .writeStream.option("checkpointLocation", checkpoint)  # tracks which files were loaded
    .option("mergeSchema", "true")
    .trigger(availableNow=True)  # load everything waiting, then stop
    .toTable(target)
)
query.awaitTermination()

# COMMAND ----------

# MAGIC %md
# MAGIC If a new column shows up in later files, this run stops once with a schema-change error. **Just run it again** and the new column is added.
# MAGIC Values that don't fit the inferred types land in the `_rescued_data` column instead of being dropped.

# COMMAND ----------

display(spark.sql(f"SELECT COUNT(*) AS rows, COUNT(DISTINCT _source_file) AS files FROM {target}"))
display(spark.table(target).limit(20))
