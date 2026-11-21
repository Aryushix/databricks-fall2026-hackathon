"""One place for every name the project uses.

Notebooks, the pipeline, and the app all read their catalog, schema, table,
and endpoint names from here, so a teammate can point the whole project at a
different schema by changing one widget or one environment variable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


@dataclass(frozen=True)
class Config:
    catalog: str = field(default_factory=lambda: _env("HACKATHON_CATALOG", "workspace"))
    schema: str = field(default_factory=lambda: _env("HACKATHON_SCHEMA", "hackathon"))
    # Databricks-hosted models. Check "Serving" in your workspace for current names.
    llm_endpoint: str = field(
        default_factory=lambda: _env("HACKATHON_LLM_ENDPOINT", "databricks-gpt-oss-120b")
    )
    embedding_endpoint: str = field(
        default_factory=lambda: _env("HACKATHON_EMBEDDING_ENDPOINT", "databricks-qwen3-embedding-0-6b")
    )
    # Free Edition allows one AI Search endpoint, so the whole team shares this one.
    search_endpoint: str = field(
        default_factory=lambda: _env("HACKATHON_SEARCH_ENDPOINT", "hackathon-search")
    )

    # ---- derived names -------------------------------------------------
    @property
    def namespace(self) -> str:
        return f"{self.catalog}.{self.schema}"

    def table(self, name: str) -> str:
        return f"{self.namespace}.{name}"

    def volume_path(self, volume: str) -> str:
        return f"/Volumes/{self.catalog}/{self.schema}/{volume}"

    @property
    def raw_path(self) -> str:
        """Tabular files (CSV, JSON, Parquet), one subfolder per dataset."""
        return self.volume_path("raw")

    @property
    def docs_path(self) -> str:
        """Documents for the RAG app (PDF, DOCX, PPTX, images)."""
        return self.volume_path("docs")

    @property
    def checkpoint_path(self) -> str:
        """Auto Loader checkpoints and schema tracking. Never put data here."""
        return self.volume_path("checkpoints")

    @property
    def parsed_docs_table(self) -> str:
        return self.table("parsed_docs")

    @property
    def chunks_table(self) -> str:
        return self.table("doc_chunks")

    @property
    def index_name(self) -> str:
        return self.table("doc_chunks_index")


def from_widgets(dbutils) -> Config:
    """Create catalog/schema widgets at the top of a notebook and return a Config.

    Widgets default to the env-var / built-in defaults above, and jobs can
    override them with task parameters named `catalog` and `schema`.
    """
    defaults = Config()
    dbutils.widgets.text("catalog", defaults.catalog, "Catalog")
    dbutils.widgets.text("schema", defaults.schema, "Schema")
    return Config(catalog=dbutils.widgets.get("catalog"), schema=dbutils.widgets.get("schema"))
