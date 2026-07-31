"""diagnose.py - placeholder until the page's content is specified."""

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app_common import render_footer  # noqa: E402

st.title("Diagnose")
st.info(
    "This page is not designed yet. The underlying data is ready - 21 "
    "statistical tests in diag_tests, with coefficients, group comparisons "
    "and chart-ready points in the other diag_* tables. Say what should be "
    "shown here and it gets built next."
)

render_footer()
