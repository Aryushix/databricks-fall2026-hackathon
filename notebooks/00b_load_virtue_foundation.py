# Databricks notebook source
# MAGIC %md
# MAGIC # 00b · Load the Virtue Foundation dataset
# MAGIC
# MAGIC Copies the read-only shared tables into our writable schema `workspace.hackathon` as `bronze_<table>`.
# MAGIC Run it once; rerun any time to refresh the copies. **The data never goes into GitHub.**

# COMMAND ----------

SOURCE_CATALOG = "databricks_virtue_foundation_dataset_dais_2026"
SOURCE_SCHEMA = "virtue_foundation_dataset"
TARGET = "workspace.hackathon"

source = "`" + SOURCE_CATALOG + "`.`" + SOURCE_SCHEMA + "`"

# 1. Our writable schema and volumes
spark.sql("CREATE SCHEMA IF NOT EXISTS " + TARGET)
spark.sql("CREATE VOLUME IF NOT EXISTS " + TARGET + ".raw")
spark.sql("CREATE VOLUME IF NOT EXISTS " + TARGET + ".docs")

# 2. Copy every shared table into bronze_<name>
tables = [row.tableName for row in spark.sql("SHOW TABLES IN " + source).collect()]
print("Found in the share:", tables)

for name in tables:
    target_table = TARGET + ".bronze_" + name
    spark.sql(
        "CREATE OR REPLACE TABLE " + target_table
        + " TBLPROPERTIES ('delta.columnMapping.mode' = 'name') AS SELECT *, '"
        + name + "' AS _source_file, current_timestamp() AS _ingested_at FROM "
        + source + ".`" + name + "`"
    )
    print(target_table, "-", spark.table(target_table).count(), "rows")

# 3. Give the team access (works when run by the workspace admin)
try:
    spark.sql("GRANT USE CATALOG ON CATALOG workspace TO `account users`")
    spark.sql("GRANT ALL PRIVILEGES ON SCHEMA " + TARGET + " TO `account users`")
    print("Team access granted")
except Exception as e:
    print("Could not grant team access:", e)

# 4. Show the columns of each copy
for name in tables:
    print("\n=== bronze_" + name)
    spark.table(TARGET + ".bronze_" + name).printSchema()
