"""Streamlit chat front end for the demo.

Deployed with Databricks Apps from the repo root (see app.yaml). Runs locally too:
    pip install -r requirements.txt
    DATABRICKS_CONFIG_PROFILE=<your-profile> streamlit run app/app.py
"""

import sys
from pathlib import Path

# Make src/hackathon importable whether we run in Databricks Apps or locally.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import streamlit as st

from hackathon import rag
from hackathon.config import Config

cfg = Config()

st.set_page_config(page_title="Fall 2026 Hackathon")
st.title("Ask our documents")
st.caption(f"Index `{cfg.index_name}` · model `{cfg.llm_endpoint}`")

if "messages" not in st.session_state:
    st.session_state.messages = []

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

if question := st.chat_input("Ask a question about the documents"):
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.spinner("Searching and writing an answer..."):
            try:
                result = rag.ask(question, cfg)
                reply = result["answer"]
                if result["sources"]:
                    names = ", ".join(s.rsplit("/", 1)[-1] for s in result["sources"])
                    reply += f"\n\n**Sources:** {names}"
            except Exception as exc:  # show the error in the demo instead of a blank screen
                reply = f"Something went wrong: `{exc}`"
        st.markdown(reply)
    st.session_state.messages.append({"role": "assistant", "content": reply})
