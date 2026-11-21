-- Databricks notebook source
-- MAGIC %md
-- MAGIC # 03 · Gold tables
-- MAGIC
-- MAGIC Gold tables answer specific questions (one row per customer, per day, per product). Dashboards, Genie, and agents read these.
-- MAGIC **Add a COMMENT to every table and key column**: Genie and agents use them to decide what to query.
-- MAGIC
-- MAGIC The examples below assume `silver_orders` and `silver_customers`. Replace them with your own tables.

-- COMMAND ----------

-- MAGIC %python
-- MAGIC import os, sys
-- MAGIC sys.path.append(os.path.abspath("../src"))
-- MAGIC from hackathon import from_widgets
-- MAGIC cfg = from_widgets(dbutils)
-- MAGIC spark.sql(f"USE CATALOG {cfg.catalog}")
-- MAGIC spark.sql(f"USE SCHEMA {cfg.schema}")
-- MAGIC print(f"Using {cfg.namespace}: SQL cells below use unqualified table names")

-- COMMAND ----------

-- EXAMPLE: one row per customer. Replace with your own Gold logic.
-- CREATE OR REPLACE TABLE gold_customer_summary
-- COMMENT 'One row per customer: order count, lifetime value, recency'
-- AS
-- SELECT
--   c.customer_id,
--   c.segment,
--   count(o.order_id)                          AS orders,
--   coalesce(sum(o.amount), 0)                 AS lifetime_value,
--   max(o.order_ts)                            AS last_order_ts,
--   datediff(current_date(), max(o.order_ts))  AS days_since_last_order
-- FROM silver_customers c
-- LEFT JOIN silver_orders o USING (customer_id)
-- GROUP BY c.customer_id, c.segment;

-- COMMAND ----------

-- EXAMPLE: column comments help Genie pick the right column.
-- ALTER TABLE gold_customer_summary ALTER COLUMN lifetime_value COMMENT 'Sum of order amounts in USD';

-- COMMAND ----------

-- See what exists in the schema
SHOW TABLES;
