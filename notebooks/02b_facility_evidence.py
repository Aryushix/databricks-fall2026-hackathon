# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# MAGIC %md
# MAGIC # 02b · Facility claims, one row each → `silver_facility_evidence`
# MAGIC
# MAGIC Run after `01b_verify_locations`. Splits each facility's `specialties`, `procedure`, `equipment` and
# MAGIC `capability` lists into one row per claim, so a claim can be searched, counted and quoted on its own.
# MAGIC
# MAGIC | Column | What it holds |
# MAGIC | --- | --- |
# MAGIC | `facility_id`, `facility_name`, `facility_type` | Which facility (`unique_id`, `name`, `facilityTypeId`) |
# MAGIC | `address_city`, `address_state_or_region`, `address_country_code` | Where it says it is |
# MAGIC | `location_confidence` | From `01b`: how far to trust the location (`high` / `medium` / `low`) |
# MAGIC | `category` | Which list the claim came from |
# MAGIC | `evidence_text` | The claim as written. Specialties are turned into words (`internalMedicine` → `internal medicine`) |
# MAGIC | `source_urls` | The record's sources, to cite next to the claim |
# MAGIC
# MAGIC **A row is a claim, not a fact.** The text was extracted from the web. Whether a facility really provides a
# MAGIC treatment is decided by `src/hackathon/verification` (see `docs/TREATMENT_VERIFICATION.md`).

# COMMAND ----------

import os
import sys

from pyspark.sql import functions as F
from pyspark.sql.types import ArrayType, StringType

sys.path.append(os.path.abspath("../src"))
from hackathon import from_widgets

cfg = from_widgets(dbutils)
SOURCE_TABLES = [cfg.table("silver_facilities_verified"), cfg.table("silver_facilities")]
EVIDENCE_TABLE = cfg.table("silver_facility_evidence")
CLAIM_COLUMNS = ["specialties", "procedure", "equipment", "capability"]
# cleaned column -> evidence column
IDENTITY_COLUMNS = {
    "unique_id": "facility_id",
    "name": "facility_name",
    "facilityTypeId": "facility_type",
    "address_city": "address_city",
    "address_stateOrRegion": "address_state_or_region",
    "address_countryCode": "address_country_code",
    "location_confidence": "location_confidence",
    "source_urls": "source_urls",
}

source = next((t for t in SOURCE_TABLES if spark.catalog.tableExists(t)), None)
if source is None:
    raise ValueError("Run 01_clean_facilities and 01b_verify_locations first. Looked for: " + ", ".join(SOURCE_TABLES))
facilities = spark.table(source)
print(source, "-", facilities.count(), "facilities")

# COMMAND ----------

def parse_array_column(df, column_name):
    """array<string>, or a JSON array in a string -> trimmed, de-duplicated array<string> with no empty items."""
    if column_name not in df.columns:
        return df
    dtype = df.schema[column_name].dataType
    if isinstance(dtype, StringType):
        parsed = F.from_json(F.col(column_name), ArrayType(StringType()))
    elif isinstance(dtype, ArrayType):
        parsed = F.col(column_name).cast(ArrayType(StringType()))
    else:
        return df
    cleaned = F.transform(parsed, lambda x: F.trim(x))
    cleaned = F.filter(cleaned, lambda x: x.isNotNull() & (x != ""))
    return df.withColumn(column_name,
                         F.array_distinct(F.coalesce(cleaned, F.array().cast(ArrayType(StringType())))))


def normalize_text(column):
    """camelCase, snake_case and punctuation -> lower-case words."""
    result = F.regexp_replace(column, r"([a-z])([A-Z])", "$1 $2")
    result = F.lower(result)
    result = F.regexp_replace(result, r"[_\-]+", " ")
    result = F.regexp_replace(result, r"[^a-z0-9\s]", " ")
    result = F.regexp_replace(result, r"\s+", " ")
    return F.trim(result)


df = facilities
for column_name in CLAIM_COLUMNS:
    df = parse_array_column(df, column_name)
if "specialties" in df.columns:
    df = df.withColumn("specialties", F.transform(F.col("specialties"), lambda x: normalize_text(x)))

# COMMAND ----------

# One row per facility x category x claim
identity = [F.col(f"`{src}`").alias(dst) for src, dst in IDENTITY_COLUMNS.items() if src in df.columns]
parts = [
    df.select(*identity, F.lit(c).alias("category"), F.explode_outer(F.col(c)).alias("evidence_text"))
    .filter(F.col("evidence_text").isNotNull() & (F.trim(F.col("evidence_text")) != ""))
    for c in CLAIM_COLUMNS if c in df.columns
]
evidence = parts[0]
for part in parts[1:]:
    evidence = evidence.unionByName(part)
evidence = evidence.dropDuplicates(["facility_id", "category", "evidence_text"])

# COMMAND ----------

evidence.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(EVIDENCE_TABLE)
spark.sql(f"COMMENT ON TABLE {EVIDENCE_TABLE} IS 'One row per facility claim (specialty, procedure, equipment, "
          f"capability) from {source}. A claim extracted from web text, not a verified fact.'")
spark.sql(f"ALTER TABLE {EVIDENCE_TABLE} ALTER COLUMN category COMMENT "
          "'Which list the claim came from: specialties, procedure, equipment or capability'")
spark.sql(f"ALTER TABLE {EVIDENCE_TABLE} ALTER COLUMN evidence_text COMMENT "
          "'The claim as written in the record (specialties turned into lower-case words)'")
print(EVIDENCE_TABLE, "-", spark.table(EVIDENCE_TABLE).count(), "claims")
display(spark.sql(f"SELECT category, count(*) AS claims, count(DISTINCT facility_id) AS facilities "
                  f"FROM {EVIDENCE_TABLE} GROUP BY category ORDER BY claims DESC"))

# COMMAND ----------

display(spark.table(EVIDENCE_TABLE).limit(20))

# COMMAND ----------

# MAGIC %md
# MAGIC ### Claim status for every claim → `silver_facility_evidence_verified`
# MAGIC `claim_status` (VERIFIED_OFFICIAL, CORROBORATED_BY_DESC, UNVERIFIED_LISTING, COPIED_LISTING, CONTRADICTED),
# MAGIC `source_trust_score` and a verbatim `corroboration_quote` (`src/hackathon/verification/claim_verifier.py`).
# MAGIC Checking claims against facilities' own websites needs the crawler cache, which lives on a laptop: run
# MAGIC `tools/verify_claims.py` there and upload `evidence_verified.csv.gz` to the `raw` volume. Without that file,
# MAGIC this cell checks descriptions and copies only, so no claim can be VERIFIED_OFFICIAL.

# COMMAND ----------

import pandas as pd

from hackathon.verification.claim_verifier import ClaimVerifier, explode_claims

VERIFIED_TABLE = cfg.table("silver_facility_evidence_verified")
laptop_file = cfg.raw_path + "/evidence_verified.csv.gz"
if os.path.exists(laptop_file):
    claims_verified = pd.read_csv(laptop_file, dtype=str, keep_default_na=False)
    print("Loaded", laptop_file)
else:
    fac = facilities.select("unique_id", "name", "description", "source_urls", "officialWebsite_domain",
                            *[c for c in CLAIM_COLUMNS if c in facilities.columns]).toPandas()
    for c in [*CLAIM_COLUMNS, "source_urls"]:
        if c in fac.columns:
            fac[c] = fac[c].map(lambda v: list(v) if v is not None else [])
    claims_verified = ClaimVerifier(fac).verify(explode_claims(fac))
    print("No laptop file: descriptions and copies only")
spark.createDataFrame(claims_verified.astype(str)).write.mode("overwrite").option("overwriteSchema", "true") \
    .saveAsTable(VERIFIED_TABLE)
display(spark.sql(f"SELECT claim_status, count(*) AS claims FROM {VERIFIED_TABLE} GROUP BY 1 ORDER BY 2 DESC"))
