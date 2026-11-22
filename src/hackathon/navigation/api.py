"""FastAPI service for community and NGO workers: pick a service and a place, get facilities.

    GET  /services                 the services you can search for (verifiable ones first)
    GET  /resolve-location?q=Gaya  a town, district or PIN code -> latitude / longitude (&state= to choose)
    POST /find-facilities          {"service": "hemodialysis", "latitude": .., "longitude": .., "max_results": 5}
    GET  /health

Run locally:  PYTHONPATH=src uvicorn hackathon.navigation.api:app --port 8000
Data paths come from NAV_DATA_DIR (default ~/hackathon-data) or the NAV_* variables in store.py.
Invalid input returns 422 without echoing what was sent. Nothing typed is stored or logged.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Callable, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .contract import LocationResolution, ServiceInfo, ServiceRequest, ServiceResponse
from .service import Navigator, build_navigator

log = logging.getLogger("hackathon.navigation.api")


def create_app(navigator: Optional[Navigator] = None,
               factory: Optional[Callable[[], Navigator]] = None,
               resolver=None) -> FastAPI:
    holder: dict = {"nav": navigator, "resolver": resolver}

    def nav() -> Navigator:
        if holder["nav"] is None:
            holder["nav"] = (factory or build_navigator)()
        return holder["nav"]

    def locator():
        if holder["resolver"] is None:
            from .locate import default_resolver
            holder["resolver"] = default_resolver()
        return holder["resolver"]

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        nav()
        yield

    app = FastAPI(title="Find care", version="0.2.0", lifespan=lifespan,
                  description="Facilities near a place whose documented evidence supports a service, with the "
                              "quotes behind each match and how sure we are.")

    @app.exception_handler(RequestValidationError)
    async def _invalid(request: Request, exc: RequestValidationError):
        errors = [{"loc": list(e.get("loc", ())), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": errors})

    @app.get("/services", response_model=list[ServiceInfo])
    def services() -> list[ServiceInfo]:
        return nav().services()

    @app.post("/find-facilities", response_model=ServiceResponse)
    def find_facilities(req: ServiceRequest) -> ServiceResponse:
        try:
            return nav().find_service(req, strict=True)
        except ValueError:
            raise HTTPException(status_code=422, detail="unknown service: see GET /services")

    @app.get("/resolve-location", response_model=LocationResolution)
    def resolve_location(q: str = Query(min_length=1, max_length=200, description="Town, district or PIN code"),
                         state: Optional[str] = Query(None, max_length=60)) -> LocationResolution:
        return locator().resolve(q, state)

    @app.get("/health")
    def health() -> dict:
        return nav().health()

    return app


app = create_app()
