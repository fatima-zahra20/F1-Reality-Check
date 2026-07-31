"""
streamlit_app.py - dashboard entry point and router.

Run locally from the project root:

    streamlit run dashboard/streamlit_app.py

On Streamlit Cloud, the app's "Main file path" setting must point at
dashboard/streamlit_app.py.

This file only wires up navigation. Each page's actual content lives under
views/; anything shared between pages (the database connection, formatting
helpers, team colours) lives in app_common.py.
"""

from __future__ import annotations

import streamlit as st

st.set_page_config(
    page_title="F1 Reality Check",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ":material/name:" icons are Streamlit's built-in icon font, not emoji - the
# project avoids emoji everywhere, including navigation.
pages = [
    st.Page("views/home.py", title="Home", icon=":material/home:", default=True),
    st.Page("views/analyse.py", title="Analyse", icon=":material/bar_chart:"),
    st.Page("views/diagnose.py", title="Diagnose", icon=":material/science:"),
    st.Page("views/predict.py", title="Predict", icon=":material/insights:"),
]

st.navigation(pages).run()
