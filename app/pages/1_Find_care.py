"""Find care: describe the problem, say where you are, see nearby facilities whose verified capabilities match.

A page of the existing Streamlit app (app/app.py, deployed by app.yaml). It calls the same library
functions as the API, so both show the same result. The emergency block always renders first, even when
the location can't be worked out. Navigation help, not medical advice.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import streamlit as st

from hackathon.cleaning import STATES
from hackathon.navigation.contract import RecommendRequest
from hackathon.navigation.locate import default_resolver
from hackathon.navigation.service import build_navigator, emergency_block

ANY_STATE = "(any state)"


@st.cache_resource
def navigator():
    return build_navigator()


@st.cache_resource(show_spinner="Loading the India Post directory...")
def resolver():
    return default_resolver()


def show_emergency(block) -> None:
    st.error(block.message)
    for line in block.category_messages:
        st.error(line)
    st.markdown("  \n".join(f"**{n.number}**: {n.label}" + (f" (or {n.alt_number})" if n.alt_number else "")
                            for n in block.numbers))
    if block.transport_advice:
        st.markdown(f"**{block.transport_advice}**")


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
            with st.expander("Evidence from the facility's listing"):
                for m, e in quotes:
                    src = e.source_url or "page unknown"
                    st.markdown(f"> {e.quote}\n\n{m.status} · {e.field} · {src}")
                    for u in e.record_source_urls:
                        st.caption(f"Listing source: {u}")


def where(place: str, state: str, lat, lon):
    """((latitude, longitude) or None, what to tell the user). Renders nothing: the emergency block goes first."""
    if lat is not None and lon is not None:
        return (lat, lon), "Distances are measured from the coordinates you entered."
    if not place.strip():
        return None, "Please tell us where you are: a town, district or 6-digit PIN code."
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
st.title("Find care near you")
st.caption("This tool helps find facilities; it does not give medical advice. If this may be an emergency, call 112.")

with st.form("find"):
    message = st.text_area("What's wrong? Describe it in your own words (English or Hindi).", max_chars=2000)
    c1, c2 = st.columns([3, 2])
    place = c1.text_input("Where are you? Town, city, district or 6-digit PIN code",
                          placeholder="e.g. Gaya, or 823001", max_chars=200)
    state = c2.selectbox("State (helps with common names)", [ANY_STATE, *sorted(STATES)])
    with st.expander("Or enter exact coordinates"):
        k1, k2 = st.columns(2)
        lat = k1.number_input("Latitude", value=None, min_value=-90.0, max_value=90.0, format="%.5f")
        lon = k2.number_input("Longitude", value=None, min_value=-180.0, max_value=180.0, format="%.5f")
    st.caption("Your location is used only to measure distance for this request. It isn't stored or guessed.")
    k = st.slider("How many results", 1, 20, 5)
    submitted = st.form_submit_button("Find facilities")

if submitted:
    if not message.strip():
        st.warning("Please describe the problem. If this may be an emergency, call 112.")
    else:
        point, told = where(place, state, lat, lon)
        if point is None:
            # no location yet, but an emergency in the message still gets its block, first
            block = emergency_block(navigator().rules.screen(message), navigator().cfg)
            if block:
                show_emergency(block)
            st.warning(told)
        else:
            resp = navigator().recommend(RecommendRequest(patient_message=message, latitude=point[0],
                                                          longitude=point[1], max_results=k))
            if resp.emergency:
                show_emergency(resp.emergency)
            st.info(told)
            st.subheader({"emergency": "Emergency", "urgent": "Get checked today", "routine": "Plan a visit",
                          "unclear": "We need a little more information"}[resp.triage.urgency])
            st.write(resp.triage.reason + " " + resp.triage.advice)
            for q in resp.follow_up_questions:
                st.markdown(f"- {q}")
            nearest = [r for r in resp.recommendations if r.list == "nearest"]
            verified = [r for r in resp.recommendations if r.list == "verified"]
            fallback = [r for r in resp.recommendations if r.list == "unverified_fallback"]
            if nearest:
                st.subheader("Nearest hospitals")
                for r in nearest:
                    show_recommendation(r)
            if verified:
                st.subheader("Further away, with matching verified capability" if nearest
                             else "Facilities with matching verified capability")
                for r in verified:
                    show_recommendation(r)
            if fallback:
                st.subheader("Nearby facilities: capability not verified, call ahead")
                for r in fallback:
                    show_recommendation(r)
            if resp.coverage_note:
                st.info(resp.coverage_note)
            if resp.degraded:
                st.warning("Some parts of this answer are unavailable right now ("
                           + ", ".join(resp.degraded_reasons) + ").")
            st.caption(resp.disclaimer)
