-- Databricks notebook source
-- MAGIC %md
-- MAGIC # 04 · Enrich text columns with AI Functions
-- MAGIC
-- MAGIC Each AI Function calls a Databricks-hosted model on every row. No endpoint setup or API key needed. Requires serverless compute.
-- MAGIC
-- MAGIC **Always test on `LIMIT 20` first**, then write the result to a table once so you don't pay for inference on every query.
-- MAGIC The examples use `silver_tickets` with a text column `body`. Replace them with your table and column.

-- COMMAND ----------

-- MAGIC %python
-- MAGIC import os, sys
-- MAGIC sys.path.append(os.path.abspath("../src"))
-- MAGIC from hackathon import from_widgets
-- MAGIC cfg = from_widgets(dbutils)
-- MAGIC spark.sql(f"USE CATALOG {cfg.catalog}")
-- MAGIC spark.sql(f"USE SCHEMA {cfg.schema}")

-- COMMAND ----------

-- 1. Redact personal data before anything else sees it
-- SELECT ticket_id, ai_mask(body, array('person', 'email', 'phone')) AS body_masked
-- FROM silver_tickets LIMIT 20;

-- COMMAND ----------

-- 2. Classify into your own labels (returns VARIANT; read the label with :response[0].value)
-- SELECT ticket_id,
--        ai_classify(body, '["billing", "bug", "feature_request", "other"]', map('version', '2.1')):response[0].value::string AS category
-- FROM silver_tickets LIMIT 20;

-- COMMAND ----------

-- 3. Extract structured fields you define
-- SELECT ticket_id,
--        ai_extract(body,
--          '{"product": {"type": "string"}, "severity": {"type": "string", "description": "low, medium, or high"}}',
--          map('version', '2.0')):response AS fields
-- FROM silver_tickets LIMIT 20;

-- COMMAND ----------

-- 4. Anything else: your own prompt. failOnError => false keeps one bad row from failing the batch.
-- SELECT ticket_id,
--        ai_query('databricks-gpt-oss-120b',
--                 concat('Summarize the customer problem in one sentence: ', body),
--                 failOnError => false) AS summary
-- FROM silver_tickets LIMIT 20;

-- COMMAND ----------

-- 5. When the output looks right, save it once as a Gold table
-- CREATE OR REPLACE TABLE gold_tickets_enriched
-- COMMENT 'Support tickets with AI category and PII-masked text'
-- AS
-- SELECT ticket_id,
--        ai_mask(body, array('person', 'email', 'phone')) AS body_masked,
--        ai_classify(body, '["billing", "bug", "feature_request", "other"]', map('version', '2.1')):response[0].value::string AS category
-- FROM silver_tickets;
