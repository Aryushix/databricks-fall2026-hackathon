# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# MAGIC %md
# MAGIC # 02 · Clean Bronze → Silver (other tables)
# MAGIC
# MAGIC **Facilities are cleaned by `01_clean_facilities`, not here.** Use this notebook for
# MAGIC `india_post_pincode_directory` and the NFHS-5 table.
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
from pyspark.sql.types import StringType,ArrayType 
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

# CELL 5: Parse array columns

def parse_array_column(df, column_name):

    if column_name not in df.columns:
        return df

    dtype = df.schema[column_name].dataType

    if isinstance(dtype, StringType):
        parsed = F.from_json(
            F.col(column_name),
            ArrayType(StringType())
        )

    elif isinstance(dtype, ArrayType):
        parsed = F.col(column_name).cast(
            ArrayType(StringType())
        )

    else:
        return df

    cleaned = F.transform(
        parsed,
        lambda x: F.trim(x)
    )

    cleaned = F.filter(
        cleaned,
        lambda x: x.isNotNull() & (x != "")
    )

    return df.withColumn(
        column_name,
        F.array_distinct(
            F.coalesce(
                cleaned,
                F.array().cast(ArrayType(StringType()))
            )
        )
    )

def normalize_text(column):

    result = F.regexp_replace(
        column,
        r"([a-z])([A-Z])",
        "$1 $2"
    )

    result = F.lower(result)
    result = F.regexp_replace(result, r"[_\-]+", " ")
    result = F.regexp_replace(result, r"[^a-z0-9\s]", " ")
    result = F.regexp_replace(result, r"\s+", " ")

    return F.trim(result)






# COMMAND ----------

# dropping unnecessary columns
# Start from the cleaned DataFrame
df2 = df

columns_to_drop = [
    "source_types",
    "source_ids",
    "source_content_id",
    "content_table_id",
    "accepts_volunteers",
    "countries",
    "affiliation_type_ids",
    "recency_of_page_update",
    "distinct_social_media_presence_count",
    "affiliated_staff_presence",
    "custom_logo_presence",
    "post_metrics_most_recent_social_media_post_date",
    "post_metrics_post_count",
    "engagement_metrics_n_followers",
    "engagement_metrics_n_likes",
    "engagement_metrics_n_engagements",
    "source",
    "coordinates",
    "cluster_id",
    "_source_file",
    "phone_numbers",
    "official_phone",
    "email",
    "websites",
    "official_website",
    "facebook_link",
    "year_established",
    "address_line1",
    "address_line2",
    "address_line3",
    "address_zip_or_postcode",
    "address_country_code",
    "facility_type_id",
    "operator_type_id",
    "area",
    "number_doctors",
    "capacity",
    "numbers_of_facts_about_the_organization",
    "latitude",
    "longitude"
]

# Drop only columns that exist
columns_to_drop = [
    c for c in columns_to_drop
    if c in df2.columns
]

df2 = df2.drop(*columns_to_drop)
array_columns = [
    "specialties",
    "procedure",
    "equipment",
    "capability"
]

for column_name in array_columns:
    df2 = parse_array_column(df2, column_name)

df2 = df2.withColumn(
    "specialties",
    F.transform(
        F.col("specialties"),
        lambda x: normalize_text(x)
    )
)
df2.head()


# COMMAND ----------

from pyspark.sql import functions as F

array_columns = [
    "specialties",
    "procedure",
    "equipment",
    "capability"
]

# Keep only columns needed for evidence and facility identity
base = df2.select(
    "unique_id",
    "name",
    "organization_type",
    "address_city",
    "address_state_or_region",
    "address_country",
    "source_urls",
    *array_columns
)

# Convert each array into individual evidence rows
evidence_dfs = []

for column_name in array_columns:

    temp = (
        base
        .select(
            F.col("unique_id").alias("facility_id"),
            F.col("name").alias("facility_name"),
            F.col("organization_type"),
            F.col("address_city"),
            F.col("address_state_or_region"),
            F.col("address_country"),
            F.lit(column_name).alias("category"),
            F.explode_outer(F.col(column_name)).alias("evidence_text")
        )
        .filter(
            F.col("evidence_text").isNotNull() &
            (F.trim(F.col("evidence_text")) != "")
        )
    )

    evidence_dfs.append(temp)

# Combine all categories
evidence = evidence_dfs[0]

for temp in evidence_dfs[1:]:
    evidence = evidence.unionByName(temp)

# Remove exact duplicate evidence rows
evidence = evidence.dropDuplicates([
    "facility_id",
    "category",
    "evidence_text"
])

display(evidence)


# COMMAND ----------

EVIDENCE_TABLE = "workspace.hackathon.silver_facility_evidence"

(
    evidence.write
    .format("delta")
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable(EVIDENCE_TABLE)
)

# COMMAND ----------

silver_table = cfg.table(f"silver_{dataset}")
df.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(silver_table)

silver = spark.table(silver_table)
print(f"{silver_table}: {silver.count():,} rows (bronze had {bronze.count():,})")
null_counts = silver.select([F.sum(F.col(c).isNull().cast("int")).alias(c) for c in silver.columns])
display(null_counts)