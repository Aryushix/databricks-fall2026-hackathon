"""FastAPI service: POST /recommend-hospitals, GET /health.

Run locally:  PYTHONPATH=src uvicorn hackathon.navigation.api:app --port 8000
Data paths come from NAV_DATA_DIR (default ~/hackathon-data) or the NAV_* variables in store.py.

Invalid input returns 422. Even then, if the body carries a patient_message that trips a red flag,
the 422 body includes the emergency block: a validation error must never hide an emergency.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Callable, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .contract import RecommendRequest, RecommendResponse
from .service import Navigator, build_navigator, emergency_block

log = logging.getLogger("hackathon.navigation.api")


def create_app(navigator: Optional[Navigator] = None,
               factory: Optional[Callable[[], Navigator]] = None) -> FastAPI:
    holder: dict = {"nav": navigator}

    def nav() -> Navigator:
        if holder["nav"] is None:
            holder["nav"] = (factory or build_navigator)()
        return holder["nav"]

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        nav()                                     # fail fast: no service without the red-flag rules
        yield

    app = FastAPI(title="Symptom navigation", version="0.1.0", lifespan=lifespan,
                  description="Finds facilities whose verified capabilities match the care a message calls for. "
                              "Navigation help, not medical advice.")

    @app.exception_handler(RequestValidationError)
    async def _invalid(request: Request, exc: RequestValidationError):
        # Errors without their input values: the message must not be echoed back.
        errors = [{"loc": list(e.get("loc", ())), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
        content: dict = {"detail": errors, "emergency": None}
        try:
            body = await request.json()
            msg = body.get("patient_message") if isinstance(body, dict) else None
            if isinstance(msg, str) and msg.strip():
                n = nav()
                block = emergency_block(n.rules.screen(msg[:20000]), n.cfg)
                content["emergency"] = block.model_dump() if block else None
                content["disclaimer"] = n.cfg["disclaimer"]
        except Exception:
            log.warning("could not screen an invalid request")
        return JSONResponse(status_code=422, content=content)

    @app.post("/recommend-hospitals", response_model=RecommendResponse)
    def recommend_hospitals(req: RecommendRequest) -> RecommendResponse:
        return nav().recommend(req)

    @app.get("/health")
    def health() -> dict:
        return nav().health()

    return app


app = create_app()
