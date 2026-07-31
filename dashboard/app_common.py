"""
app_common.py - shared data access for every page of the dashboard.

streamlit_app.py is the router (st.navigation); each file under views/ is a
page it can show. Pages do not import each other, so anything more than one
of them needs (the database connection, formatting helpers, team colours)
lives here instead.
"""

from __future__ import annotations

import base64
import gzip
import io
import shutil
import sqlite3
import tempfile
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

REPO = "fatima-zahra20/F1-Reality-Check"
ASSET_URL = f"https://github.com/{REPO}/releases/download/data-latest/dashboard.db.gz"

# Resolved from this file's own location, not the current working directory,
# so it doesn't matter whether streamlit was launched from the repo root or
# from inside dashboard/ - both land on the same two paths.
APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent
ASSETS_DIR = APP_DIR / "assets"
LOCAL_DB = PROJECT_ROOT / "outputs" / "dashboard" / "dashboard.db"

# Fallback for teams whose colour is missing in the source data.
NEUTRAL = "#8A8A94"


# --- data -----------------------------------------------------------------

@st.cache_resource(show_spinner="Loading race data…")
def get_connection() -> sqlite3.Connection:
    """
    One connection per server process, not per session - the download and the
    23 MB file are shared by every visitor rather than repeated for each.
    """
    if LOCAL_DB.exists():
        path = LOCAL_DB
    else:
        tmp = Path(tempfile.gettempdir()) / "f1_reality_check_dashboard.db"
        if not tmp.exists():
            r = requests.get(ASSET_URL, timeout=120)
            if r.status_code == 404:
                st.error(
                    "The data has not been published yet.\n\n"
                    "Run `python pipeline/s06_publish.py --execute` to upload it "
                    f"to the `data-latest` release of `{REPO}`."
                )
                st.stop()
            r.raise_for_status()
            with gzip.open(io.BytesIO(r.content), "rb") as src, open(tmp, "wb") as dst:
                shutil.copyfileobj(src, dst)
        path = tmp

    # check_same_thread=False: Streamlit serves reruns from a worker pool, so
    # the connection is touched by threads other than the one that opened it.
    return sqlite3.connect(path, check_same_thread=False)


@st.cache_data(show_spinner=False)
def query(sql: str, params: tuple = ()) -> pd.DataFrame:
    return pd.read_sql(sql, get_connection(), params=params)


@st.cache_data(show_spinner=False)
def team_colours() -> dict[str, str]:
    df = query("SELECT team_name, team_colour FROM dim_team")
    return {
        r.team_name: (f"#{r.team_colour}" if pd.notna(r.team_colour) else NEUTRAL)
        for r in df.itertuples()
    }


# --- formatting -------------------------------------------------------------

def fmt_lap(seconds) -> str:
    """Lap times read as m:ss.sss, never as a raw float."""
    if seconds is None or pd.isna(seconds):
        return "-"
    m, s = divmod(float(seconds), 60)
    return f"{int(m)}:{s:06.3f}"


def fmt_gap(seconds, laps) -> str:
    if pd.notna(laps) and laps:
        return f"+{int(laps)} lap" + ("s" if laps > 1 else "")
    if pd.isna(seconds):
        return "-"
    return "-" if seconds == 0 else f"+{seconds:.3f}s"


# --- assets ------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def find_asset_b64(stem: str) -> str | None:
    """
    Looks for assets/<stem>.{jpg,jpeg,png,webp} and returns it as a base64
    data URI, or None if none exist.

    Base64-embedding rather than a static file URL because Streamlit Cloud
    needs enableStaticServing configured to serve plain files, and a data URI
    works identically local and deployed with no extra config.
    """
    mime = {".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".webp": "webp"}
    for ext, kind in mime.items():
        path = ASSETS_DIR / f"{stem}{ext}"
        if path.exists():
            data = base64.b64encode(path.read_bytes()).decode()
            return f"data:image/{kind};base64,{data}"
    return None


# --- footer -------------------------------------------------------------------

def render_footer() -> None:
    """Author credit, shown at the bottom of every page for consistency."""
    st.divider()
    st.markdown(
        """
        <div style="text-align:center; color:#6B6B76; font-size:0.85rem;
                    padding: 0 0 1.5rem;">
            Data Analyst<br>
            Boutkhil Fatima Zahra<br>
            <a href="https://github.com/fatima-zahra20" target="_blank"
               style="color:#6B6B76;">GitHub</a>
            &nbsp;&middot;&nbsp;
            <a href="https://www.linkedin.com/in/fatima-zahra-boutkhil-393011309/"
               target="_blank" style="color:#6B6B76;">LinkedIn</a>
        </div>
        """,
        unsafe_allow_html=True,
    )
