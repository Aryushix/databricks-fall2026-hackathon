"""Small optional config endpoint for the teammate's React/Vite frontend.

Import into your EXISTING api.py; do not replace api.py or the orchestrator.
This endpoint intentionally never exposes GEMINI_API_KEY.
"""

import os

from dotenv import load_dotenv

load_dotenv()

def attach_frontend_config(app, *, demo_mode=True):
    @app.get("/api/config")
    def frontend_config():
        demo_searches = []
        if demo_mode:
            try:
                from mock_services import MOCK_HOSPITALS
                demo_searches = [
                    {"service": service, "location": str(city).title()}
                    for service, city in MOCK_HOSPITALS.keys()
                ]
            except (ImportError, AttributeError, TypeError, ValueError):
                # The orchestrator may use a different mock data shape.
                demo_searches = []

        return {
            # This must be a referrer-restricted *browser* Maps key,
            # NEVER a server-side Gemini key.
            "maps_browser_key": os.getenv("MAPS_BROWSER_KEY", ""),
            "maps_map_id": os.getenv("MAPS_MAP_ID") or "DEMO_MAP_ID",
            "demo_mode": demo_mode,
            "demo_searches": demo_searches,
        }
