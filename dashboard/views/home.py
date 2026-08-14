"""
home.py - the landing page.

Falls back to a plain dark gradient if assets/f1_landing.(jpg|png|jpeg|webp)
is not present, so the page still looks intentional without it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app_common import find_asset_b64, query, render_footer  # noqa: E402

# Counted, not written down. This line used to read "70 races. 217,000 laps.",
# which was 2023 to 2025 exactly: it was true when written and silently stopped
# being true when 2026 started arriving, while the sidebar, which counts, said
# 81. A headline number nobody recomputes drifts from the data behind it after
# every race weekend, and this one is the first thing a visitor reads.
#
# Race laps rather than all laps, because the count sits beside the race count
# and has to mean the same thing: practice and qualifying laps would treble it
# and correspond to nothing on the page.
#
# This is the first page a visitor hits, and reading the counts means opening
# the bundle, which on Streamlit Cloud downloads it. That cost is moved earlier
# rather than added: every other page needs the same connection, and it is
# cached per server process, so only the first visitor waits. The fallback below
# means a slow or failed download degrades to a sentence without numbers instead
# of an error page on the front door.
WORDS = {1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six"}

try:
    _span = query(
        "SELECT MIN(year) lo, MAX(year) hi, COUNT(*) races FROM dim_race")
    _laps = int(query("SELECT COUNT(*) n FROM fact_lap").n.iloc[0])
    _seasons = int(_span.hi.iloc[0]) - int(_span.lo.iloc[0]) + 1
    # Rounded down to the nearest thousand: a headline that moves by about 60
    # every race reads as a live counter rather than a statement of scale.
    HEADLINE = (f"{WORDS.get(_seasons, _seasons)} seasons. "
                f"{int(_span.races.iloc[0])} races. "
                f"{_laps // 1000:,},000 laps. Every claim tested.")
except Exception:
    HEADLINE = "Every claim tested."

hero_image = find_asset_b64("f1_landing")
background = (
    f'linear-gradient(rgba(10,10,14,.60), rgba(10,10,14,.72)), url("{hero_image}")'
    if hero_image
    else "linear-gradient(135deg, #15151E, #2A2A38)"
)

# Google Fonts, not a bundled file: no CSP restriction applies to a normal
# Streamlit page, and the font-family fallback below already covers the case
# where the request fails or is blocked.
st.markdown(
    '<link href="https://fonts.googleapis.com/css2?family=Titillium+Web:'
    'wght@400;600;700;800&display=swap" rel="stylesheet">',
    unsafe_allow_html=True,
)

# Hides the default header and sidebar on this page only - Analyse/Diagnose/
# Predict each render their own sidebar, but a landing page reads cleaner
# without Streamlit's toolbar chrome, and without a padded, width-capped
# content column, since the photo is meant to fill the browser window.
st.markdown(
    """
    <style>
    [data-testid="stHeader"] { display: none; }
    [data-testid="stSidebar"] { display: none; }
    [data-testid="stMainBlockContainer"] {
        padding: 0 !important;
        max-width: 100% !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    f"""
    <style>
    .st-key-hero {{
        background-image: {background};
        background-size: cover;
        background-position: center;
        min-height: 100vh;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        padding: 2rem;
        text-align: center;
        font-family: 'Titillium Web', sans-serif;
    }}
    .st-key-hero [data-testid="stMarkdownContainer"] {{
        width: 100%;
        text-align: center !important;
    }}
    .st-key-hero h1 {{
        color: #FFFFFF;
        font-family: 'Titillium Web', sans-serif;
        font-size: clamp(2.2rem, 4vw, 3rem);
        font-weight: 800;
        letter-spacing: 0.04em;
        text-align: center !important;
        margin: 0 0 1.25rem 0;
    }}
    .st-key-hero p.subtitle {{
        color: #FFFFFF;
        font-family: 'Titillium Web', sans-serif;
        font-size: 1.3rem;
        font-weight: 600;
        text-align: center !important;
        margin: 0 0 1.25rem 0;
    }}
    .st-key-hero p.note {{
        color: #FFFFFF;
        font-family: 'Titillium Web', sans-serif;
        font-size: 1rem;
        font-weight: 600;
        text-align: center !important;
        max-width: 640px;
        margin: 0 auto 1.25rem auto;
        line-height: 1.5;
    }}
    .st-key-hero div[data-testid="stHorizontalBlock"] {{
        max-width: 640px;
        margin: 0 auto;
    }}
    .st-key-hero div[data-testid="column"] button {{
        background-color: #E10600;
        color: #FFFFFF;
        border: none;
        font-family: 'Titillium Web', sans-serif;
        font-size: 1.1rem;
        font-weight: 700;
        padding: 0.9rem 0;
    }}
    .st-key-hero div[data-testid="column"] button:hover {{
        background-color: #B80500;
        color: #FFFFFF;
    }}
    </style>
    """,
    unsafe_allow_html=True,
)

with st.container(key="hero"):
    st.markdown(
        f"""
        <h1>F1 REALITY CHECK</h1>
        <p class="subtitle">What actually decides a Formula 1 race?</p>
        <p class="note">{HEADLINE}</p>
        <p class="note">Every finding on this dashboard is the output of a
        statistical test, not an observation. Where the evidence is weak,
        the dashboard says so.</p>
        """,
        unsafe_allow_html=True,
    )

    # Paths are relative to the entry point (streamlit_app.py at the repo
    # root), not to this file, and must match its st.Page() paths exactly.
    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("Analyse", width="stretch"):
            st.switch_page("dashboard/views/analyse.py")
    with c2:
        if st.button("Diagnose", width="stretch"):
            st.switch_page("dashboard/views/diagnose.py")
    with c3:
        if st.button("Predict", width="stretch"):
            st.switch_page("dashboard/views/predict.py")

render_footer()
