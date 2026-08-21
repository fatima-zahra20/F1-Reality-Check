"""
url_state.py - selections that survive a reload.

WHY THIS EXISTS
---------------
Streamlit keeps widget values in `st.session_state`, and a browser reload starts
a NEW session. So every choice a reader made, the season, the race, the driver,
the lap, went back to its default on F5. The theme did the same until it was
moved into the query string, and this is that fix generalised: the URL is the
only thing a reload carries.

It also makes a page shareable. A link now reopens on the race and driver the
sender was looking at, which a screenshot cannot do.

HOW IT IS USED
--------------
Two calls per widget, on either side of it:

    url_state.restore("prescribe_race", int, valid=set(in_year.session_key))
    session_key = st.selectbox(..., key="prescribe_race")
    ...
    url_state.remember("prescribe_race")

`restore` runs BEFORE the widget, because Streamlit reads session_state when it
builds one, and seeding it afterwards would be a rerun too late. `remember` runs
after, when session_state holds what the reader actually chose.

WHY `valid` IS NOT OPTIONAL IN PRACTICE
---------------------------------------
A query string is user input. Someone can edit it, a link can go stale, and a
race that existed last month may not be in the picker today. Handing Streamlit a
selectbox value that is not among its options raises and takes the page down.
So a restored value is checked against the real options and dropped if it does
not belong, which turns a bad URL into a default rather than an error page.

NONE ROUND-TRIPS AS AN EMPTY STRING. "All cars" is a real, chosen state on the
Prescribe page and has to survive a reload like any other, so it is written as
an empty value and read back as None rather than being treated as absent.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

import streamlit as st


def as_bool(raw: str) -> bool:
    """Query strings are text; "False" is text and therefore truthy."""
    return str(raw).lower() in ("1", "true", "yes", "on")


def clamp(key: str, lo: float, hi: float) -> None:
    """
    Pull a stored slider value back inside a range that has since shrunk.

    Streamlit takes a widget's value from session_state when the key is present
    and IGNORES the `value` argument, so a stored number outside the slider's
    current bounds raises rather than being corrected. That happens without any
    URL involved: Prescribe's "allow values from any race" toggle widens every
    lever, and turning it back off narrows them under whatever was chosen.
    """
    if key not in st.session_state:
        return
    current = st.session_state[key]
    if current is None:
        return
    try:
        bounded = min(max(current, lo), hi)
    except TypeError:
        return
    if bounded != current:
        st.session_state[key] = type(current)(bounded)


def restore(key: str, cast: Callable[[str], Any] = str,
            valid: Iterable | Callable[[Any], bool] | None = None) -> None:
    """
    Seed session_state[key] from the query string, once per session.

    Does nothing when the key is already set, so a reader's later choices are
    never overwritten by the URL they arrived on.
    """
    if key in st.session_state:
        return

    raw = st.query_params.get(key)
    if raw is None:
        return

    if raw == "":
        # Checked against `valid` like any other value: an empty parameter only
        # means None where None is genuinely one of the options.
        if valid is None or None in valid:
            st.session_state[key] = None
        return

    try:
        value = cast(raw)
    except (TypeError, ValueError):
        # A malformed parameter is not worth an error page. Fall through and
        # let the widget use its own default.
        return

    # `valid` may be a container or a predicate. Sliders need the second: their
    # range is continuous, so "is this one of the options" is the wrong question
    # and "is it between the ends" is the right one.
    if valid is not None:
        ok = valid(value) if callable(valid) else (value in valid)
        if not ok:
            return

    st.session_state[key] = value


def remember(*keys: str) -> None:
    """
    Mirror the current value of each key into the query string.

    Called after the widgets have rendered. Keys with no value yet are skipped
    rather than written as empty, so a page that has not reached a widget does
    not put a misleading parameter in the address bar.
    """
    for key in keys:
        if key not in st.session_state:
            continue
        value = st.session_state[key]
        st.query_params[key] = "" if value is None else str(value)
