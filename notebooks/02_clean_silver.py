# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# MAGIC %md
# MAGIC # 02 · Clean Bronze → Silver (other tables)
# MAGIC
# MAGIC **Facilities are cleaned by `01_clean_facilities`, not here.** Use this notebook for
# MAGIC `india_post_pincode_directory` and the NFHS-5 table. The one-row-per-claim facility table
# MAGIC (`silver_facility_evidence`) is built by `02b_facility_evidence`.
# MAGIC
# MAGIC Generic cleaning that's safe for almost any dataset, then a clearly marked spot for your dataset's own rules:
# MAGIC 1. `snake_case` column names
# MAGIC 2. Trim text, turn empty strings into NULL
# MAGIC 3. Drop rows missing the key, keep the latest copy of each key (or drop exact duplicates)
# MAGIC 4. **Your rules**: casts, valid ranges, standardized values
# MAGIC
# MAGIC Writes `silver_<dataset>` and prints a before/after quality report.

# COMMAND ----------

import os
import re
import sys

from pyspark.sql import Window
from pyspark.sql import functions as F

sys.path.append(os.path.abspath("../src"))
from hackathon import from_widgets

cfg = from_widgets(dbutils)
dbutils.widgets.text("dataset", "india_post_pincode_directory", "Table name after bronze_")
dbutils.widgets.text("keys", "", "Key column(s), comma-separated (optional)")
dataset = dbutils.widgets.get("dataset")
keys = [k.strip() for k in dbutils.widgets.get("keys").split(",") if k.strip()]
if dataset == "facilities":
    raise ValueError("Facilities have their own rules: run 01_clean_facilities instead.")

bronze = spark.table(cfg.table(f"bronze_{dataset}"))
print(f"bronze_{dataset}: {bronze.count():,} rows, {len(bronze.columns)} columns")

# COMMAND ----------

# MAGIC %md ### Profile first
# MAGIC Look at nulls, distinct counts, and odd values before you write rules.

# COMMAND ----------

dbutils.data.summarize(bronze)

# COMMAND ----------

def snake(name: str) -> str:
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)  # camelCase -> camel_Case
    return re.sub(r"[^0-9a-zA-Z]+", "_", name).strip("_").lower()


df = bronze

# 1. snake_case names (leave metadata columns such as _source_file alone)
df = df.toDF(*[c if c.startswith("_") else snake(c) for c in df.columns])
keys = [snake(k) for k in keys]

# 2. trim text and turn "" into NULL
for col_name, col_type in df.dtypes:
    if col_type == "string" and not col_name.startswith("_"):
        trimmed = F.trim(F.col(col_name))
        df = df.withColumn(col_name, F.when(trimmed == "", None).otherwise(trimmed))

# 3. keys: drop rows without one, keep the most recently ingested copy
if keys:
    df = df.dropna(subset=keys)
    latest_first = Window.partitionBy(*keys).orderBy(F.col("_ingested_at").desc())
    df = df.withColumn("_rn", F.row_number().over(latest_first)).filter("_rn = 1").drop("_rn")
else:
    df = df.dropDuplicates([c for c in df.columns if not c.startswith("_")])

# COMMAND ----------

# MAGIC %md ### 4. Your dataset's rules
# MAGIC Edit this cell. Examples are commented out.

# COMMAND ----------

# df = (df
#     .withColumn("order_ts", F.expr("try_cast(order_ts AS TIMESTAMP)"))   # NULL instead of an error on bad values
#     .withColumn("amount",   F.expr("try_cast(amount AS DECIMAL(10,2))"))
#     .withColumn("email",    F.lower("email"))
#     .withColumn("state",    F.upper("state"))
#     .filter(F.col("amount") > 0)
# )

# COMMAND ----------

silver_table = cfg.table(f"silver_{dataset}")
df.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(silver_table)

silver = spark.table(silver_table)
print(f"{silver_table}: {silver.count():,} rows (bronze had {bronze.count():,})")
null_counts = silver.select([F.sum(F.col(c).isNull().cast("int")).alias(c) for c in silver.columns])
display(null_counts)
