"""Lakeflow Spark Declarative Pipeline: raw files -> Bronze -> Silver, with data-quality rules.

The scheduled version of notebooks 01-02. Use one or the other for a dataset.
Tables are prefixed `pl_` so they never collide with the notebook-built ones.

Pipeline settings (Configuration), all optional:
    hackathon.catalog   default: workspace
    hackathon.schema    default: hackathon
    hackathon.dataset   folder under raw/, default: orders
    hackathon.format    csv | json | parquet, default: csv
    hackathon.key       key column for the not-null rule, default: id
"""

from pyspark import pipelines as dp
from pyspark.sql import functions as F

CATALOG = spark.conf.get("hackathon.catalog", "workspace")
SCHEMA = spark.conf.get("hackathon.schema", "hackathon")
DATASET = spark.conf.get("hackathon.dataset", "orders")
FILE_FORMAT = spark.conf.get("hackathon.format", "csv")
KEY = spark.conf.get("hackathon.key", "id")
SOURCE = f"/Volumes/{CATALOG}/{SCHEMA}/raw/{DATASET}/"


@dp.table(name=f"pl_bronze_{DATASET}", comment=f"Raw {DATASET} files, loaded incrementally")
def bronze():
    reader = (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", FILE_FORMAT)
        .option("cloudFiles.inferColumnTypes", "true")
    )
    if FILE_FORMAT == "csv":
        reader = reader.option("header", "true")
    return reader.load(SOURCE).selectExpr(
        "*", "_metadata.file_path AS _source_file", "current_timestamp() AS _ingested_at"
    )


@dp.table(name=f"pl_silver_{DATASET}", comment=f"Cleaned {DATASET}")
@dp.expect_or_drop("has_key", f"{KEY} IS NOT NULL")
# Add your own rules. expect = keep + count, expect_or_drop = remove, expect_or_fail = stop.
# @dp.expect_or_drop("positive_amount", "amount > 0")
# @dp.expect("valid_email", "email LIKE '%@%'")
def silver():
    df = spark.readStream.table(f"pl_bronze_{DATASET}")
    for col_name, col_type in df.dtypes:
        if col_type == "string" and not col_name.startswith("_"):
            trimmed = F.trim(F.col(col_name))
            df = df.withColumn(col_name, F.when(trimmed == "", None).otherwise(trimmed))
    return df
