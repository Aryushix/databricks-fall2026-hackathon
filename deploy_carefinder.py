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

from api import app  # noqa: E402

if not (FRONTEND / "index.html").exists():
    raise RuntimeError("React build missing. Run npm run build in the frontend folder.")

# Register after the API routes so /api/process and /api/voice still work.
app.mount("/", StaticFiles(directory=str(FRONTEND), html=True), name="frontend")

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("DATABRICKS_APP_PORT", "8000")),
    )
