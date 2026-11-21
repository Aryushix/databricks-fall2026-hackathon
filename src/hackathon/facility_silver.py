"""Databricks side of the facility cleaning: pick a source, clean it, write silver_facilities.

The cleaning rules themselves live in `cleaning.py` (shared with tools/clean_dataset.py).
This module only moves data between Spark and pandas and gives the result proper types.

Source priority (first one found wins), all in the `raw` volume of our schema:
  1. facilities_enriched.csv : already cleaned locally AND enriched by tools/facility_crawler.py
  2. facilities_cleaned.csv  : already cleaned locally by tools/clean_dataset.py
  3. dataset.csv             : the raw export; cleaned here, identical to the local script's output
  4. bronze_facilities table : the copy of the share; cleaned here, but the 30 records that were
                               split across lines in the source lose their last 23 fields
                               (they are kept and flagged `row_truncated_in_source`)
"""

from __future__ import annotations

import os

import pandas as pd
from pyspark.sql import functions as F
from pyspark.sql import types as T

from . import cleaning

PRECLEANED_FILES = ["facilities_enriched.csv", "facilities_cleaned.csv"]
RAW_FILE = "dataset.csv"
HELPER_COLUMNS = ["_source_file", "_ingested_at"]  # added by 00b, not part of the dataset

INT_COLUMNS = ["yearEstablished", *cleaning.INT_COLS, "dq_flag_count"]
DOUBLE_COLUMNS = ["latitude", "longitude"]
BOOL_COLUMNS = list(cleaning.BOOL_COLS)
DATE_COLUMNS = ["post_metrics_most_recent_social_media_post_date", "recency_of_page_update"]
ARRAY_COLUMNS = [*cleaning.LIST_COLS, "specialties", "phone_numbers", "email_all"]

COLUMN_COMMENTS = {
    "unique_id": "Facility id from the Virtue Foundation dataset",
    "name": "Facility name as listed in the source",
    "name_short": "Short display name, only set when the name looked like it contained an address",
    "description": "Free-text description extracted from the web: the main evidence to cite",
    "specialties": "Specialties the facility claims (camelCase taxonomy where possible)",
    "capability": "Capabilities the facility claims: a claim to verify, not ground truth",
    "procedure": "Procedures the facility claims",
    "equipment": "Equipment the facility claims",
    "address_city": "City, with common aliases normalised (Bangalore to Bengaluru, Gurgaon to Gurugram)",
    "address_stateOrRegion": "Canonical state; may be inferred or corrected from the PIN (see dq_flags)",
    "address_zipOrPostcode": "6-digit Indian PIN code; NULL when invalid",
    "latitude": "Latitude; NULL when the point fell outside India",
    "longitude": "Longitude; NULL when the point fell outside India",
    "officialPhone": "Main phone in E.164 format (+91...)",
    "officialPhone_type": "mobile, landline, toll_free, shared_cost, foreign, other or invalid",
    "numberDoctors": "Number of doctors; implausible values (5000+) removed",
    "capacity": "Bed capacity; implausible values (10000+) removed",
    "yearEstablished": "Year established; values before 1600 or in the future removed",
    "source_urls": "Web pages the record was extracted from: cite these as evidence",
    "dup_group_id": "Same value = likely the same facility listed twice (same name or phone within 300 m)",
    "dq_flags": "Semicolon-separated data-quality issues and fixes for this row (see docs/DATA_CLEANING.md)",
    "dq_flag_count": "Number of data-quality flags on this row",
}


def _as_text(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def bronze_to_pandas(sdf) -> pd.DataFrame:
    """Spark table -> all-text pandas frame shaped like a CSV read (what cleaning.py expects)."""
    cols = []
    for field in sdf.schema.fields:
        if field.name in HELPER_COLUMNS:
            continue
        c = F.col(f"`{field.name}`")
        if isinstance(field.dataType, (T.ArrayType, T.MapType, T.StructType)):
            c = F.to_json(c)
        elif isinstance(field.dataType, T.TimestampType):
            c = F.date_format(c, "yyyy-MM-dd")
        else:
            c = c.cast("string")
        cols.append(c.alias(field.name))
    return sdf.select(cols).toPandas()


def load_source(spark, raw_dir: str, bronze_table: str):
    """Return (frame, repaired_ids, extra_flags, already_clean, description)."""
    for name in PRECLEANED_FILES:
        path = os.path.join(raw_dir, name)
        if os.path.exists(path):
            df = pd.read_csv(path, dtype=str, keep_default_na=False, low_memory=False)
            df = df.replace({"": None})
            return df, set(), {}, True, f"already-cleaned file {path}"
    path = os.path.join(raw_dir, RAW_FILE)
    if os.path.exists(path):
        df, repaired = cleaning.read_and_repair(path)
        return df, repaired, {}, False, f"raw file {path} ({len(repaired)} split records repaired)"
    df, repaired, extra, info = cleaning.prepare_parsed_rows(bronze_to_pandas(spark.table(bronze_table)))
    desc = (f"table {bronze_table} ({info['fragment_rows_dropped']} fragment rows dropped, "
            f"{info['truncated_records_flagged']} truncated records flagged)")
    return df, repaired, extra, False, desc


def to_silver(spark, df: pd.DataFrame):
    """Cleaned pandas frame -> typed Spark DataFrame (lists become array<string>)."""
    text = pd.DataFrame({c: [_as_text(v) for v in df[c]] for c in df.columns}, dtype=object)
    text = text.astype(object).where(text.notna(), None)
    schema = T.StructType([T.StructField(c, T.StringType(), True) for c in text.columns])
    sdf = spark.createDataFrame(text, schema=schema)
    typed = []
    for c in sdf.columns:
        col = F.col(f"`{c}`")
        if c in INT_COLUMNS:
            col = F.expr(f"try_cast(`{c}` AS BIGINT)")
        elif c in DOUBLE_COLUMNS:
            col = F.expr(f"try_cast(`{c}` AS DOUBLE)")
        elif c in BOOL_COLUMNS:
            col = F.expr(f"try_cast(lower(`{c}`) AS BOOLEAN)")
        elif c in DATE_COLUMNS:
            col = F.expr(f"try_cast(`{c}` AS DATE)")
        elif c in ARRAY_COLUMNS:
            col = F.from_json(col, "array<string>")
        typed.append(col.alias(c))
    return sdf.select(typed)


def add_comments(spark, table: str) -> None:
    spark.sql(f"COMMENT ON TABLE {table} IS 'Cleaned Virtue Foundation facilities: one row per facility. "
              "Every field is a claim extracted from the web; check dq_flags before trusting it.'")
    existing = set(spark.table(table).columns)
    for col, text in COLUMN_COMMENTS.items():
        if col in existing:
            safe = text.replace("'", "\\'")
            spark.sql(f"ALTER TABLE {table} ALTER COLUMN `{col}` COMMENT '{safe}'")


def dq_summary(sdf):
    """One row per data-quality flag with how many facilities carry it."""
    return (sdf.select(F.explode(F.split(F.coalesce(F.col("dq_flags"), F.lit("")), ";")).alias("flag"))
            .where(F.col("flag") != "")
            .groupBy("flag").count()
            .withColumnRenamed("count", "facilities")
            .orderBy(F.col("facilities").desc()))
