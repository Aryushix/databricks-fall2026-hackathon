# Databricks notebook source
# MAGIC %md
# MAGIC # 05 · Build the RAG index
# MAGIC
# MAGIC Turns the documents in the `docs` volume into a searchable index:
# MAGIC 1. **Parse** new files with `ai_parse_document` → `parsed_docs` (each file is parsed only once)
# MAGIC 2. **Chunk** with `ai_prep_search` → `doc_chunks` (Change Data Feed on, so the index syncs incrementally)
# MAGIC 3. **Index** with AI Search (formerly Vector Search): Databricks computes the embeddings
# MAGIC
# MAGIC Rerun the notebook after adding documents. Free Edition allows **one** AI Search endpoint, so the team shares one.

# COMMAND ----------

# MAGIC %pip install -U -q databricks-ai-search

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import os
import sys

sys.path.append(os.path.abspath("../src"))
from hackathon import from_widgets

cfg = from_widgets(dbutils)
dbutils.widgets.text("search_endpoint", cfg.search_endpoint, "AI Search endpoint")
endpoint = dbutils.widgets.get("search_endpoint")

files = [f for f in dbutils.fs.ls(cfg.docs_path) if not f.name.endswith("/")]
if not files:
    raise ValueError(f"No documents in {cfg.docs_path}. Upload PDFs/DOCX/PPTX there first.")
print(f"{len(files)} files in {cfg.docs_path}")

# COMMAND ----------

# MAGIC %md ### 1. Parse new documents

# COMMAND ----------

spark.sql(f"CREATE TABLE IF NOT EXISTS {cfg.parsed_docs_table} (path STRING, parsed VARIANT)")
spark.sql(f"""
INSERT INTO {cfg.parsed_docs_table}
SELECT path, ai_parse_document(content, map('version', '2.0')) AS parsed
FROM read_files('{cfg.docs_path}/', format => 'binaryFile')
WHERE path NOT IN (SELECT path FROM {cfg.parsed_docs_table})
""")

display(spark.sql(f"""
SELECT path, parsed:error_status AS error
FROM {cfg.parsed_docs_table}
WHERE parsed:error_status IS NOT NULL
"""))  # empty result = every file parsed cleanly

# COMMAND ----------

# MAGIC %md ### 2. Chunk into search-ready passages

# COMMAND ----------

spark.sql(f"""
CREATE TABLE IF NOT EXISTS {cfg.chunks_table} (
  chunk_pk          STRING COMMENT 'Stable id: hash of file path + chunk position',
  chunk_to_embed    STRING COMMENT 'Text used to compute the embedding',
  chunk_to_retrieve STRING COMMENT 'Text passed to the LLM',
  source_uri        STRING COMMENT 'Volume path of the source document'
)
TBLPROPERTIES (delta.enableChangeDataFeed = true)
""")
spark.sql(f"ALTER TABLE {cfg.chunks_table} SET TBLPROPERTIES (delta.enableChangeDataFeed = true)")

spark.sql(f"""
MERGE INTO {cfg.chunks_table} AS t
USING (
  WITH prepped AS (
    SELECT path, ai_prep_search(parsed) AS prepped FROM {cfg.parsed_docs_table}
  )
  SELECT
    md5(concat_ws('#', path, chunk.value:chunk_position::STRING)) AS chunk_pk,
    chunk.value:chunk_to_embed::STRING    AS chunk_to_embed,
    chunk.value:chunk_to_retrieve::STRING AS chunk_to_retrieve,
    path                                  AS source_uri
  FROM prepped, LATERAL variant_explode(prepped:document.contents) AS chunk
) AS s
ON t.chunk_pk = s.chunk_pk
WHEN MATCHED AND t.chunk_to_retrieve <> s.chunk_to_retrieve THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *
WHEN NOT MATCHED BY SOURCE THEN DELETE
""")

display(spark.sql(f"""
SELECT source_uri, COUNT(*) AS chunks FROM {cfg.chunks_table} GROUP BY source_uri ORDER BY chunks DESC
"""))

# COMMAND ----------

# MAGIC %md ### 3. Create or sync the AI Search index

# COMMAND ----------

from databricks.ai_search.client import AISearchClient

client = AISearchClient()

if not client.endpoint_exists(endpoint):
    print(f"Creating endpoint '{endpoint}'. This can take several minutes...")
    client.create_endpoint_and_wait(name=endpoint, endpoint_type="STANDARD")

if client.index_exists(endpoint_name=endpoint, index_name=cfg.index_name):
    index = client.get_index(endpoint_name=endpoint, index_name=cfg.index_name)
    index.sync()
    print("Index exists: started a sync with the latest chunks")
else:
    print("Creating the index and embedding every chunk...")
    index = client.create_delta_sync_index_and_wait(
        endpoint_name=endpoint,
        index_name=cfg.index_name,
        primary_key="chunk_pk",
        source_table_name=cfg.chunks_table,
        pipeline_type="TRIGGERED",  # syncs when this notebook runs, not continuously
        embedding_source_column="chunk_to_embed",
        embedding_model_endpoint_name=cfg.embedding_endpoint,
    )

index.wait_until_ready(wait_for_updates=True)
print(f"Index ready: {cfg.index_name}")

# COMMAND ----------

# MAGIC %md ### 4. Smoke test
# MAGIC Change the question to something your documents should answer.

# COMMAND ----------

display(spark.sql(f"""
SELECT source_uri, left(chunk_to_retrieve, 300) AS preview
FROM vector_search(
  index       => '{cfg.index_name}',
  query_text  => 'What is this document about?',
  num_results => 5)
"""))
