"""predict.py - placeholder until a predictive model exists."""

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app_common import render_footer  # noqa: E402

st.title("Predict")
st.info(
    "There is no predictive model yet. This page will show win-probability "
    "predictions once one is built, validated, and its calibration is "
    "checked - not before."
)

render_footer()
