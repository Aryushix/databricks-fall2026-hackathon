import os
import sys
from pathlib import Path

import uvicorn
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent
CARE_FINDER = ROOT / "app" / "carefinder"
FRONTEND = CARE_FINDER / "frontend" / "dist"

sys.path.insert(0, str(CARE_FINDER))
sys.path.insert(0, str(ROOT / "src"))

from api import app

if not (FRONTEND / "index.html").exists():
    raise RuntimeError("React frontend build missing")

app.mount("/", StaticFiles(directory=str(FRONTEND), html=True), name="frontend")

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("DATABRICKS_APP_PORT", "8000")),
    )
