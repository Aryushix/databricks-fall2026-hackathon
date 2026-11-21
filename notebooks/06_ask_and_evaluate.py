# Databricks notebook source
# MAGIC %md
# MAGIC # 06 · Ask questions and evaluate
# MAGIC
# MAGIC Uses `src/hackathon/rag.py`, the same code the demo app runs.
# MAGIC 1. Ask a question and inspect the trace (retrieved chunks → prompt → answer)
# MAGIC 2. Score answers on the test set in `eval/questions.jsonl` with MLflow LLM judges
# MAGIC
# MAGIC Rerun step 2 after every change (prompt, chunking, model, hybrid search) and compare runs in the Experiment UI.

# COMMAND ----------

# MAGIC %pip install -U -q "mlflow[databricks]>=3.1" openai databricks-sdk

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import json
import os
import sys

import mlflow

sys.path.append(os.path.abspath("../src"))
from hackathon import from_widgets, rag

cfg = from_widgets(dbutils)

# Notebooks in Git folders log to a shared workspace experiment.
user = spark.sql("SELECT current_user()").first()[0]
mlflow.set_experiment(f"/Users/{user}/hackathon-rag")
print(f"Index: {cfg.index_name} · Model: {cfg.llm_endpoint}")

# COMMAND ----------

# MAGIC %md ### 1. Ask one question

# COMMAND ----------

result = rag.ask("What is this document about?", cfg)
print(result["answer"])
print("\nSources:", *result["sources"], sep="\n  ")

# COMMAND ----------

# MAGIC %md ### 2. Evaluate on the test set
# MAGIC Edit `eval/questions.jsonl` with 20–50 real questions and the facts a good answer must contain. Lines starting with `TODO` are skipped.

# COMMAND ----------

from mlflow.genai.scorers import (
    Correctness,
    Guidelines,
    RelevanceToQuery,
    RetrievalGroundedness,
    Safety,
)

with open("../eval/questions.jsonl") as f:
    eval_data = [json.loads(line) for line in f if line.strip()]
eval_data = [row for row in eval_data if not row["inputs"]["question"].startswith("TODO")]
if not eval_data:
    raise ValueError("Add real questions to eval/questions.jsonl first.")

results = mlflow.genai.evaluate(
    data=eval_data,
    predict_fn=lambda question: rag.answer(question, cfg),
    scorers=[
        Correctness(),            # covers the expected_facts?
        RelevanceToQuery(),       # answers what was asked?
        RetrievalGroundedness(),  # supported by the retrieved chunks?
        Safety(),
        Guidelines(name="cites_source", guidelines="The answer cites at least one source file in square brackets."),
    ],
)
display(results.tables["eval_results"])
