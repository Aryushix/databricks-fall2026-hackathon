# Databricks notebook source
# MAGIC %md
# MAGIC # 07 · Verify treatments → `silver_treatment_verification`
# MAGIC
# MAGIC Runs the treatment verifier (`src/hackathon/verification`, see `docs/TREATMENT_VERIFICATION.md`) on
# MAGIC `silver_facilities_verified` and the crawler's cached official pages. It can run as a notebook or as a
# MAGIC **job task** (every setting is a widget, so job parameters override them).
# MAGIC
# MAGIC Writes:
# MAGIC - `records.jsonl.gz`, `summary.json` and `run_log.jsonl` to the output folder in the volume, in the layout
# MAGIC   the symptom-navigation app reads (`NAV_DATA_DIR/verification/`);
# MAGIC - the Delta table `silver_treatment_verification`: one row per facility and treatment, with the status,
# MAGIC   confidence, reason codes, review flags and the full evidence.
# MAGIC
# MAGIC Before the first run, upload `crawl.sqlite` (from `tools/facility_crawler.py` on a laptop) to the `raw` volume.
# MAGIC Without it the run still works, but nothing can reach CONFIRMED (that needs the facility's own website).
# MAGIC
# MAGIC Written from the Databricks docs; not yet run on our workspace. If worker processes fail on serverless,
# MAGIC set `workers` to 1: the output is identical, only slower.

# COMMAND ----------

# MAGIC %pip install -q pyyaml beautifulsoup4 phonenumbers

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import gzip
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.append(os.path.abspath("../src"))
from hackathon.config import from_widgets
from hackathon.verification import runlog
from hackathon.verification.pipeline import Verifier

cfg = from_widgets(dbutils)
dbutils.widgets.text("source_table", cfg.table("silver_facilities_verified"), "Facilities table")
dbutils.widgets.text("crawl_db", cfg.volume_path("raw") + "/crawl.sqlite", "crawl.sqlite in a volume")
dbutils.widgets.text("out_dir", cfg.volume_path("raw") + "/navigation/verification", "Output folder (volume)")
dbutils.widgets.text("treatments", "", "KB ids, comma-separated (empty: all)")
dbutils.widgets.text("workers", "1", "Processes for the rules pass")
dbutils.widgets.dropdown("llm", "false", ["false", "true"], "Add the model pass")

SOURCE = dbutils.widgets.get("source_table")
CRAWL_DB = dbutils.widgets.get("crawl_db")
OUT_DIR = dbutils.widgets.get("out_dir")
TREATMENTS = [t.strip() for t in dbutils.widgets.get("treatments").split(",") if t.strip()] or None
WORKERS = int(dbutils.widgets.get("workers"))
USE_LLM = dbutils.widgets.get("llm") == "true"
TABLE = cfg.table("silver_treatment_verification")

# COMMAND ----------

# 1. Facilities, in the same shape as the CSV the CLI reads: strings, JSON lists, None for empty
pdf = spark.table(SOURCE).toPandas()


def as_text(v):
    if v is None:
        return None
    if hasattr(v, "tolist") and not isinstance(v, str):
        v = v.tolist()
    if isinstance(v, (list, tuple)):
        return json.dumps([x for x in v if x is not None], ensure_ascii=False)
    s = str(v)
    return None if s in ("", "nan", "NaT", "None") else s


facilities = pdf.astype(object).apply(lambda col: col.map(as_text))
print(f"{len(facilities)} facilities from {SOURCE}")

# COMMAND ----------

# 2. Local working copies: SQLite needs a real file system, not the volume's
work = Path(tempfile.mkdtemp(prefix="verify-"))
crawl_local = None
if os.path.exists(CRAWL_DB):
    crawl_local = work / "crawl.sqlite"
    shutil.copy(CRAWL_DB, crawl_local)
else:
    print(f"No crawl.sqlite at {CRAWL_DB}: running on extracted fields only (nothing can be CONFIRMED)")
os.makedirs(OUT_DIR, exist_ok=True)
cache_local = work / "labels.sqlite"
if os.path.exists(f"{OUT_DIR}/labels.sqlite"):
    shutil.copy(f"{OUT_DIR}/labels.sqlite", cache_local)

# COMMAND ----------

# 3. Run
run_id = runlog.setup("text", work / "run_log.jsonl")
v = Verifier(concepts=TREATMENTS, use_llm=USE_LLM, cache_path=cache_local, workers=WORKERS)
v.build(facilities, crawl_local)
counts = {}
with gzip.open(work / "records.jsonl.gz", "wt", encoding="utf-8") as fh:
    for rec in v.run(TREATMENTS):
        key = f"{rec['normalized_treatment']['concept_id']}|{rec['verification_status']}"
        counts[key] = counts.get(key, 0) + 1
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
if hasattr(v.classifier, "commit"):
    v.classifier.commit()
summary = {"records": sum(counts.values()), "kb_version": v.kb_version, "pipeline_version": v.cfg["pipeline_version"],
           "config_hash": v.config_hash, "classifier": [v.classifier.model_id, v.classifier.version],
           "status_counts": dict(sorted(counts.items())), "run_id": run_id}
(work / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
for name in ("records.jsonl.gz", "summary.json", "run_log.jsonl", "labels.sqlite"):
    if (work / name).exists():
        shutil.copy(work / name, f"{OUT_DIR}/{name}")
print(json.dumps(summary, indent=2))

# COMMAND ----------

# 4. Delta table: one row per facility and treatment; nested evidence kept as structs
from pyspark.sql import functions as F

raw = spark.read.json(f"{OUT_DIR}/records.jsonl.gz")
table = raw.select(
    "hospital_id", "hospital_name", "facility_type",
    F.col("normalized_treatment.concept_id").alias("treatment_id"),
    F.col("normalized_treatment.name").alias("treatment_name"),
    F.col("normalized_treatment.kb_version").alias("kb_version"),
    F.col("normalized_treatment.kb_review_status").alias("kb_review_status"),
    "verification_status", "confidence_score", "confidence_basis", "reason_codes", "requires_manual_review",
    "review_reasons", "reasoning_summary", "missing_prerequisites", "hospital_location", "direct_evidence",
    "supporting_evidence", "contradictory_evidence", "evidence_counts", "pipeline_version", "config_hash", "run_timestamp")
table.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(TABLE)
spark.sql(f"COMMENT ON TABLE {TABLE} IS 'Treatment verification: one row per facility and treatment. "
          "Describes what documented evidence supports; not a guarantee a treatment is clinically available.'")
display(spark.table(TABLE).groupBy("treatment_id", "verification_status").count().orderBy("treatment_id", "verification_status"))
