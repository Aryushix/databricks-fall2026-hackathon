"""Structured run logs for batch verification runs and Databricks jobs.

Every stage logs an event with counts and timings. The console gets readable text (or JSON with
--log-format json); a run's out-dir always gets run_log.jsonl, one JSON object per line, so runs can be
compared and audited later. Logs carry counts and ids only, never claim text.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

RUN_ID = uuid.uuid4().hex[:12]


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        d = {"ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
             "level": record.levelname, "logger": record.name, "run_id": RUN_ID, "message": record.getMessage()}
        d.update(getattr(record, "fields", None) or {})
        if record.exc_info:
            d["exception"] = self.formatException(record.exc_info)
        return json.dumps(d, ensure_ascii=False, default=str)


def setup(fmt: str = "text", log_file: Optional[str | Path] = None, level: int = logging.INFO) -> str:
    """Configure the root logger: console in `fmt` (text or json), plus a JSON-lines file if given."""
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    console = logging.StreamHandler()
    console.setFormatter(JsonFormatter() if fmt == "json" else
                         logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(console)
    if log_file:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(JsonFormatter())
        root.addHandler(fh)
    root.setLevel(level)
    return RUN_ID


def event(logger: logging.Logger, name: str, **fields) -> None:
    """Log a named stage event: readable text on the console, every field kept in JSON."""
    text = " ".join(f"{k}={v}" for k, v in fields.items())
    logger.info("%s %s", name, text, extra={"fields": {"event": name, **fields}})
