"""Retrieval-augmented answers over the documents in the AI Search index.

Used by notebook 06 (evaluation) and by the Streamlit app, so both always
run the same logic. Authentication comes from the environment: your user in a
notebook, the app's service principal in Databricks Apps, or a CLI profile
when you run locally.
"""

from __future__ import annotations

from functools import lru_cache

from databricks.sdk import WorkspaceClient

from .config import Config

# MLflow tracing is optional: it records each step in notebooks, and the app
# keeps working without mlflow installed.
try:
    import mlflow

    trace = mlflow.trace
except ImportError:  # pragma: no cover

    def trace(func=None, **_kwargs):
        if func is None:
            return lambda f: f
        return func


SYSTEM_PROMPT = (
    "You answer questions using only the context provided. "
    "Cite the source file for each fact in square brackets, e.g. [report.pdf]. "
    "If the context does not contain the answer, say you don't know."
)


@lru_cache(maxsize=1)
def _workspace() -> WorkspaceClient:
    return WorkspaceClient()


def _text_from_content(content) -> str:
    """Chat responses are usually a string; some reasoning models return a list of parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            kind = part.get("type") if isinstance(part, dict) else getattr(part, "type", None)
            text = part.get("text") if isinstance(part, dict) else getattr(part, "text", None)
            if kind == "text" and text:
                parts.append(text)
        return "".join(parts)
    return "" if content is None else str(content)


@trace(span_type="RETRIEVER")
def retrieve(question: str, cfg: Config | None = None, k: int = 5) -> list[dict]:
    """Return the top-k chunks as MLflow-style documents."""
    cfg = cfg or Config()
    response = _workspace().vector_search_indexes.query_index(  # AI Search (formerly Vector Search)
        index_name=cfg.index_name,
        columns=["chunk_to_retrieve", "source_uri"],
        query_text=question,
        num_results=k,
        query_type="HYBRID",  # vector + keyword: better for IDs, names, exact terms
    )
    rows = (response.result.data_array or []) if response.result else []
    return [{"page_content": row[0], "metadata": {"doc_uri": row[1]}} for row in rows]


@trace
def ask(question: str, cfg: Config | None = None) -> dict:
    """Answer a question. Returns {"answer": str, "sources": [doc_uri, ...]}."""
    cfg = cfg or Config()
    docs = retrieve(question, cfg)
    if not docs:
        return {"answer": "I couldn't find anything relevant in the indexed documents.", "sources": []}

    context = "\n\n---\n\n".join(
        f"[{d['metadata']['doc_uri'].rsplit('/', 1)[-1]}] {d['page_content']}" for d in docs
    )
    llm = _workspace().serving_endpoints.get_open_ai_client()
    response = llm.chat.completions.create(
        model=cfg.llm_endpoint,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"},
        ],
    )
    answer = _text_from_content(response.choices[0].message.content)
    sources = sorted({d["metadata"]["doc_uri"] for d in docs})
    return {"answer": answer, "sources": sources}


def answer(question: str, cfg: Config | None = None) -> str:
    """Just the answer text. This is the function `mlflow.genai.evaluate` calls."""
    return ask(question, cfg)["answer"]
