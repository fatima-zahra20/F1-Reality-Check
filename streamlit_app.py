"""
streamlit_app.py - dashboard entry point and router.

    streamlit run streamlit_app.py

This file sits at the repo root rather than beside the rest of the dashboard
because Streamlit Cloud fixes an app's "Main file path" when it is first
deployed and no longer exposes that setting for editing. Moving the entry
point would mean deleting and redeploying the app, so the entry point stays
where Cloud already looks for it. Everything else lives in dashboard/.

st.Page paths are resolved relative to this file, which is why they carry the
dashboard/ prefix. st.switch_page() in views/home.py uses the same strings.

Each page's content lives under dashboard/views/; anything shared between
pages (the database connection, formatting helpers, team colours) lives in
dashboard/app_common.py.
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
    st.Page("dashboard/views/home.py", title="Home",
            icon=":material/home:", default=True),
    st.Page("dashboard/views/analyse.py", title="Analyse",
            icon=":material/bar_chart:"),
    st.Page("dashboard/views/diagnose.py", title="Diagnose",
            icon=":material/science:"),
    st.Page("dashboard/views/predict.py", title="Predict",
            icon=":material/insights:"),
    st.Page("dashboard/views/prescribe.py", title="Prescribe",
            icon=":material/timer:"),
]

st.navigation(pages).run()
