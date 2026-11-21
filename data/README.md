# Data does not go in Git

Keep datasets in Unity Catalog volumes so the whole team reads the same copy, and so GitHub never stores large or private files. `.gitignore` blocks everything in this folder except this README.

| What | Where (default names) |
| --- | --- |
| Tabular files (CSV, JSON, Parquet) | `/Volumes/workspace/hackathon/raw/<dataset_name>/` |
| Documents for the RAG app (PDF, DOCX, PPTX, images) | `/Volumes/workspace/hackathon/docs/` |

**Upload:** in Databricks, open **Catalog → workspace → hackathon → raw** (or **docs**) and click **Upload to this volume**. Create a subfolder per dataset under `raw/`.

**Record your sources here** so judges and teammates know where the data came from:

| Dataset | Source / URL | License | Who added it |
| --- | --- | --- | --- |
| TODO | | | |
