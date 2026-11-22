"""Find care: describe the problem, share a location, see nearby facilities whose verified capabilities match.

A page of the existing Streamlit app (app/app.py, deployed by app.yaml). It calls the same library
function as the API, so both show the same result. The emergency block always renders first.
Navigation help, not medical advice.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import streamlit as st

from hackathon.navigation.contract import RecommendRequest
from hackathon.navigation.service import build_navigator


@st.cache_resource
def navigator():
    return build_navigator()


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


st.set_page_config(page_title="Find care")
st.title("Find care near you")
st.caption("This tool helps find facilities; it does not give medical advice. If this may be an emergency, call 112.")

with st.form("find"):
    message = st.text_area("What's wrong? Describe it in your own words (English or Hindi).", max_chars=2000)
    c1, c2 = st.columns(2)
    lat = c1.number_input("Latitude", value=None, min_value=-90.0, max_value=90.0, format="%.5f")
    lon = c2.number_input("Longitude", value=None, min_value=-180.0, max_value=180.0, format="%.5f")
    st.caption("Your location is used only to measure distance for this request. It isn't stored or guessed.")
    k = st.slider("How many results", 1, 20, 5)
    submitted = st.form_submit_button("Find facilities")

if submitted:
    if not message.strip() or lat is None or lon is None:
        st.warning("Please describe the problem and enter your latitude and longitude. If this may be an emergency, call 112.")
    else:
        resp = navigator().recommend(RecommendRequest(patient_message=message, latitude=lat, longitude=lon, max_results=k))
        if resp.emergency:
            st.error(resp.emergency.message)
            for line in resp.emergency.category_messages:
                st.error(line)
            st.markdown("  \n".join(f"**{n.number}**: {n.label}" + (f" (or {n.alt_number})" if n.alt_number else "")
                                    for n in resp.emergency.numbers))
            if resp.emergency.transport_advice:
                st.markdown(f"**{resp.emergency.transport_advice}**")
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
            st.warning("Some parts of this answer are unavailable right now (" + ", ".join(resp.degraded_reasons) + ").")
        st.caption(resp.disclaimer)
