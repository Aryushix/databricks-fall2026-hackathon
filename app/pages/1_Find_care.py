"""Find care: a community or NGO worker picks a service and a place, and sees nearby facilities.

A page of the existing Streamlit app (app/app.py, deployed by app.yaml). It calls the same library
functions as the API (POST /find-facilities), so both show the same result. No symptoms, no triage, no
model at request time: every status was computed in advance by the treatment verifier.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import streamlit as st

from hackathon.cleaning import STATES
from hackathon.navigation.contract import ServiceRequest
from hackathon.navigation.locate import default_resolver
from hackathon.navigation.service import build_navigator

ANY_STATE = "(any state)"


@st.cache_resource(show_spinner="Loading facility data...")
def navigator():
    return build_navigator(use_llm=False)


@st.cache_resource(show_spinner="Loading the India Post directory...")
def resolver():
    return default_resolver()


def show_recommendation(r) -> None:
    with st.container(border=True):
        st.markdown(f"**{r.rank}. {r.hospital_name or r.hospital_id}** · {r.facility_type or 'facility'} · "
                    f"{r.straight_line_distance_km} km straight-line")
        st.write(r.explanation)
        if r.listed_phone:
            st.markdown(f"Phone: **{r.listed_phone}** ({r.listed_phone_note})")
        for w in r.warnings:
            st.caption(f"⚠ {w}")
        quotes = [(m, e) for m in r.matched_care_needs for e in m.evidence]
        if quotes:
            with st.expander("Evidence from the facility's own website or listing"):
                for m, e in quotes:
                    src = e.source_url or "page unknown"
                    st.markdown(f"> {e.quote}\n\n{m.status} · {e.field} · {src}")
                    for u in e.record_source_urls:
                        st.caption(f"Listing source: {u}")


def where(place: str, state: str, lat, lon):
    """((latitude, longitude) or None, what to tell the user)."""
    if lat is not None and lon is not None:
        return (lat, lon), "Distances are measured from the coordinates you entered."
    if not place.strip():
        return None, "Please enter a town, district or 6-digit PIN code."
    loc = resolver().resolve(place, None if state == ANY_STATE else state)
    if loc.status == "ambiguous":
        states = ", ".join(sorted({c.state for c in loc.candidates}))
        return None, f"{loc.note} ({states}) Pick it in the State box and search again."
    if loc.status != "found":
        return None, loc.note
    told = f"Distances are measured from {loc.label}"
    if loc.spread_km:
        told += f"; its post offices are within about {loc.spread_km:g} km of that point"
    return (loc.latitude, loc.longitude), told + "." + (f" {loc.note}" if loc.note else "")


st.set_page_config(page_title="Find care")
st.title("Find a facility for a service")
st.caption("For community health workers and NGO coordinators. Results show what the documented evidence "
           "supports; call the facility to confirm before sending anyone.")

services = navigator().services()
labels = {f"{s.name}{' ✓ verifiable' if s.verifiable else ''}": s for s in services}

with st.form("find"):
    choice = st.selectbox("Service", list(labels),
                          help="✓ verifiable: we checked facilities' own websites and listings for this service. "
                               "For the others we can only list nearby facilities to call.")
    c1, c2 = st.columns([3, 2])
    place = c1.text_input("Where? Town, city, district or 6-digit PIN code", placeholder="e.g. Gaya, or 823001",
                          max_chars=200)
    state = c2.selectbox("State (helps with common names)", [ANY_STATE, *sorted(STATES)])
    with st.expander("Or enter exact coordinates"):
        k1, k2 = st.columns(2)
        lat = k1.number_input("Latitude", value=None, min_value=-90.0, max_value=90.0, format="%.5f")
        lon = k2.number_input("Longitude", value=None, min_value=-180.0, max_value=180.0, format="%.5f")
    k = st.slider("How many results", 1, 20, 5)
    submitted = st.form_submit_button("Find facilities")

if submitted:
    service = labels[choice]
    point, told = where(place, state, lat, lon)
    if point is None:
        st.warning(told)
    else:
        resp = navigator().find_service(ServiceRequest(service=service.id, latitude=point[0], longitude=point[1],
                                                       max_results=k))
        st.info(told)
        verified = [r for r in resp.recommendations if r.list == "verified"]
        fallback = [r for r in resp.recommendations if r.list != "verified"]
        if verified:
            st.subheader(f"Facilities with documented {resp.service_name}")
            st.caption("CONFIRMED: the facility's own website says so. LIKELY: the listing says so, but we couldn't "
                       "check the facility's website.")
            for r in verified:
                show_recommendation(r)
        if fallback:
            st.subheader("Nearby facilities to call: this service isn't verified for them")
            for r in fallback:
                show_recommendation(r)
        if resp.coverage_note:
            st.info(resp.coverage_note)
        if resp.degraded:
            st.warning("Some parts of this answer are unavailable right now (" + ", ".join(resp.degraded_reasons) + ").")
        st.caption(resp.disclaimer)
