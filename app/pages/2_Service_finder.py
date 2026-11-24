"""Healthcare service finder for community health workers (navigation/matcher.py, configs/services.yaml).

Pick a service, a place and a radius (up to 150 km). Services with a verifier KB show CONFIRMED / LIKELY facilities
with their evidence; the others show SEMANTIC_MATCH facilities whose listed claims match the service, labeled not
verified. Data: NAV_DATA_DIR with facilities_verified.csv, verification/records.jsonl.gz and, for semantic matches,
evidence_verified.csv.gz from tools/verify_claims.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import pandas as pd
import streamlit as st

from hackathon.cleaning import STATES
from hackathon.navigation.locate import default_resolver
from hackathon.navigation.matcher import ServiceMatcher, load_services

ANY_STATE = "(any state)"
FILTERS = {"All matches": (0.0, {"CONFIRMED", "LIKELY", "SEMANTIC_MATCH"}),
           "Confirmed & likely only": (0.0, {"CONFIRMED", "LIKELY"}),
           "Confirmed only": (0.0, {"CONFIRMED"})}
BADGE = {"CONFIRMED": "✅ CONFIRMED", "LIKELY": "🟡 LIKELY", "SEMANTIC_MATCH": "⚪ LISTED CLAIMS (not verified)"}
TRUST = {1.0: "Verified official site", 0.6: "Unrecognised site", 0.5: "Shared / chain site", 0.45: "Directory listing"}


@st.cache_resource(show_spinner="Loading facility data...")
def matcher():
    return ServiceMatcher()


@st.cache_resource(show_spinner="Loading the India Post directory...")
def resolver():
    return default_resolver()


st.set_page_config(page_title="Service finder")
st.title("Healthcare Service Finder for Community Health Workers")
st.caption("Locate verified facilities and capabilities within 150 km across India. Results describe documented "
           "evidence, not live availability: call the facility before sending anyone.")

services = load_services()["services"]
with st.form("finder"):
    service = st.selectbox("Select required service", list(services),
                           format_func=lambda s: services[s]["name"] + (" ✓ verifiable" if services[s].get("kb_id") else ""))
    c1, c2 = st.columns([3, 2])
    place = c1.text_input("Town, district or 6-digit PIN code", placeholder="e.g. Pune, or 411001", max_chars=200)
    state = c2.selectbox("State (helps with common names)", [ANY_STATE, *sorted(STATES)])
    with st.expander("Or enter exact coordinates"):
        k1, k2 = st.columns(2)
        lat = k1.number_input("Latitude", value=None, min_value=-90.0, max_value=90.0, format="%.5f")
        lon = k2.number_input("Longitude", value=None, min_value=-180.0, max_value=180.0, format="%.5f")
    radius = st.slider("Radius (km)", min_value=10, max_value=150, value=100, step=10)
    filt = st.selectbox("Verification filter", list(FILTERS))
    submitted = st.form_submit_button("Find facilities")

if submitted:
    point = None
    if lat is not None and lon is not None:
        point = (lat, lon)
    elif place.strip():
        loc = resolver().resolve(place, None if state == ANY_STATE else state)
        if loc.status == "found":
            point = (loc.latitude, loc.longitude)
            st.info(f"Searching around {loc.label}." + (f" {loc.note}" if loc.note else ""))
        elif loc.status == "ambiguous":
            st.warning(f"{loc.note} ({', '.join(sorted({c.state for c in loc.candidates}))}) Pick it in the State box.")
        else:
            st.warning(loc.note)
    else:
        st.warning("Enter a town, district or PIN code, or coordinates.")
    if point:
        floor, tiers = FILTERS[filt]
        res = [r for r in matcher().match(service, point[0], point[1], radius_km=radius, min_confidence=floor,
                                          max_results=50) if r.verification_tier in tiers][:15]
        if not res:
            st.info("No facility in our data matches within this radius. Try a larger radius or the 'All matches' filter.")
        for r in res:
            with st.container(border=True):
                st.markdown(f"**{r.hospital_name or r.hospital_id}** · {r.facility_type or 'facility'} · "
                            f"{r.distance_km} km · {BADGE[r.verification_tier]} {r.overall_confidence:.0%}")
                if r.official_phone:
                    st.markdown(f"Phone: **{r.official_phone}** (from the facility listing; not checked by us)")
                for w in r.warnings:
                    st.caption(f"⚠ {w}")
                with st.expander("Evidence & matched claims"):
                    if r.matched_claims:
                        st.dataframe(pd.DataFrame([{
                            "Pillar": c.pillar, "Claim": c.claim_text, "Status": c.claim_status,
                            "Source": TRUST.get(round(c.source_trust_score, 2), f"trust {c.source_trust_score:.2f}"),
                            "Link": c.source_url or ""} for c in r.matched_claims]), hide_index=True)
                    for q in r.corroborating_quotes:
                        st.markdown(f"> {q}")
