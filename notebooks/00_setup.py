# Databricks notebook source
# MAGIC %md
# MAGIC # 00 · Setup
# MAGIC
# MAGIC Creates the schema and the three volumes the rest of the project uses. **One person runs this once** per workspace.
# MAGIC
# MAGIC | Volume | Put here |
# MAGIC | --- | --- |
# MAGIC | `raw` | Tabular files, one subfolder per dataset: `raw/orders/*.csv`, `raw/patients/*.json` |
# MAGIC | `docs` | Documents for the RAG app: PDF, DOCX, PPTX, PNG/JPG |
# MAGIC | `checkpoints` | Auto Loader state. Leave it alone. |

# COMMAND ----------

import os
import sys

sys.path.append(os.path.abspath("../src"))
from hackathon import from_widgets

cfg = from_widgets(dbutils)
print(f"Using {cfg.namespace}")

# COMMAND ----------

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {cfg.namespace} COMMENT 'Fall 2026 hackathon'")
for volume in ["raw", "docs", "checkpoints"]:
    spark.sql(f"CREATE VOLUME IF NOT EXISTS {cfg.namespace}.{volume}")

print("Ready. Upload files to:")
print(f"  {cfg.raw_path}/<dataset_name>/   (CSV, JSON, Parquet)")
print(f"  {cfg.docs_path}/                 (PDF, DOCX, PPTX, images)")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Optional: let every teammate use the schema
# MAGIC If teammates were invited to this workspace and can't see the tables, a workspace admin can run:
# MAGIC ```sql
# MAGIC GRANT USE CATALOG ON CATALOG workspace TO `account users`;
# MAGIC GRANT ALL PRIVILEGES ON SCHEMA workspace.hackathon TO `account users`;
# MAGIC ```
