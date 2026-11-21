-- Load the Virtue Foundation data with the SQL Editor (no notebook needed).
--
-- Use this when a notebook cell stays on "Waiting". The SQL Editor runs on the
-- Serverless Starter Warehouse, a separate compute from notebooks. It creates the
-- same bronze_ tables as notebooks/00b_load_virtue_foundation.
--
-- How to run:
--   1. Left sidebar -> SQL Editor. Pick "Serverless Starter Warehouse" at the top.
--   2. Paste this whole file.
--   3. Run STEP 1 alone first. Find the table that starts with nfhs_5_district_health_.
--   4. Replace all three NFHS_NAME below with that exact name.
--   5. Click Run all.

-- STEP 1: list the shared tables
SHOW TABLES IN databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset;

-- STEP 2: our writable schema and volumes
CREATE SCHEMA IF NOT EXISTS workspace.hackathon COMMENT 'Fall 2026 hackathon';
CREATE VOLUME IF NOT EXISTS workspace.hackathon.raw;
CREATE VOLUME IF NOT EXISTS workspace.hackathon.docs;

-- STEP 3: copy each shared table (columnMapping keeps column names that contain spaces or parentheses)
CREATE OR REPLACE TABLE workspace.hackathon.bronze_facilities
TBLPROPERTIES ('delta.columnMapping.mode' = 'name') AS
SELECT *, 'facilities' AS _source_file, current_timestamp() AS _ingested_at
FROM databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.facilities;

CREATE OR REPLACE TABLE workspace.hackathon.bronze_india_post_pincode_directory
TBLPROPERTIES ('delta.columnMapping.mode' = 'name') AS
SELECT *, 'india_post_pincode_directory' AS _source_file, current_timestamp() AS _ingested_at
FROM databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.india_post_pincode_directory;

CREATE OR REPLACE TABLE workspace.hackathon.bronze_NFHS_NAME
TBLPROPERTIES ('delta.columnMapping.mode' = 'name') AS
SELECT *, 'NFHS_NAME' AS _source_file, current_timestamp() AS _ingested_at
FROM databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.NFHS_NAME;

-- STEP 4: give the team access (workspace admin only)
GRANT USE CATALOG ON CATALOG workspace TO `account users`;
GRANT ALL PRIVILEGES ON SCHEMA workspace.hackathon TO `account users`;

-- STEP 5: check it worked
SHOW TABLES IN workspace.hackathon;
SELECT 'bronze_facilities' AS table_name, COUNT(*) AS row_count FROM workspace.hackathon.bronze_facilities
UNION ALL
SELECT 'bronze_india_post_pincode_directory', COUNT(*) FROM workspace.hackathon.bronze_india_post_pincode_directory;
