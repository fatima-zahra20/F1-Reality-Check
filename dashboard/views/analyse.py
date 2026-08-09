"""
analyse.py - the descriptive layer, told as three stories about one race.

    Story of a Race     the whole field, race-wide
    Story of a Driver   one driver's race, through their eyes
    Story of a Team     both cars compared, the teammate lens

All three share the same Year and Race filter; Driver and Team add one more.
Each story answers the questions in
DESCRIPTIVE ANALYTICS/descriptive_question_bank.md, in the order the bank
asks them, so the page reads chronologically rather than by chart type.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import story_driver  # noqa: E402
import story_race  # noqa: E402
import story_team  # noqa: E402
from app_common import query, render_footer  # noqa: E402

# --- shared filters -----------------------------------------------------------

races = query("""
    SELECT session_key, year, round, race_name, circuit, country, race_date,
           total_laps, entrants, dnf_count, safety_car_periods, vsc_periods,
           red_flag_periods, avg_track_temp, avg_air_temp, pct_samples_wet,
           is_wet_race, circuit_type
    FROM dim_race
    ORDER BY year DESC, round
""")

st.sidebar.title("F1 Reality Check")
st.sidebar.caption(f"{len(races)} races · {races.year.min()}-{races.year.max()}")

season = st.sidebar.selectbox(
    "Season", sorted(races.year.unique(), reverse=True), key="season_choice",
)
season_races = races[races.year == season]

# Options are session_keys, not names: two seasons share race names, and the
# label becomes a lookup instead of a filter over the frame.
labels = {
    int(r.session_key): f"R{int(r.round):02d}  {r.race_name}"
    for r in season_races.itertuples()
}
options = list(labels)

# Changing season leaves the previous season's race in session state, which is
# no longer a valid option. Reset it before the widget renders - Streamlit
# will otherwise try to format a value it cannot find and raise.
if st.session_state.get("race_choice") not in options:
    st.session_state["race_choice"] = options[0]

session_key = st.sidebar.selectbox(
    "Race",
    options,
    format_func=lambda k: labels.get(k, str(k)),
    key="race_choice",
)
race = season_races[season_races.session_key == session_key].iloc[0]


# --- story picker -------------------------------------------------------------

STORIES = ["Story of a Race", "Story of a Driver", "Story of a Team"]
# st.radio rather than st.segmented_control: the pill styling is nicer, but
# Streamlit 1.51's own AppTest harness cannot model a segmented_control's
# state, which breaks every automated check of this page after the first
# render. A testable page is worth more than the pills.
story = st.radio(
    "Story", STORIES, horizontal=True, label_visibility="collapsed",
    key="story_choice",
)

st.title(story)
st.caption(
    f"{race.race_name} · {race.circuit}, {race.country} · "
    f"{pd.to_datetime(race.race_date).strftime('%d %B %Y')} · "
    f"{race.circuit_type} circuit"
)


# --- section picker -----------------------------------------------------------
# Every section of the chosen story renders, in order, on one scroll. Each maps
# to a file in DESCRIPTIVE ANALYTICS and answers that file's questions. Sections
# differ by story, since a team has no "gaps" questions and only a driver has a
# teammate.
#
# THE SECTION LIST DOES NOT FILTER, it aims. Nothing is hidden any more, so it
# marks where you are and jumps you where you want to be. See the block below
# for why it is hand-written markup rather than an st.radio.
#
# The hand-over from Diagnose still works. That page sets section_choice
# directly, so arriving here still opens on the section it sent you to, and now
# the rest of the story is visible around it rather than hidden behind it.
#
# Rendering all ten sections costs 1.31s cold and nothing once cached, so there
# was no performance reason to keep them apart.

MODULES = {
    "Story of a Race": story_race,
    "Story of a Driver": story_driver,
    "Story of a Team": story_team,
}
section_pairs = MODULES[story].section_options()
section_titles = {key: title for key, title in section_pairs}
section_keys = list(section_titles)

# Switching story changes which sections exist, and Diagnose can hand over a
# section directly. Fall back to the first section rather than raising when the
# stored key is not valid here.
if st.session_state.get("section_choice") not in section_keys:
    st.session_state["section_choice"] = section_keys[0]

st.sidebar.divider()
section = st.session_state["section_choice"]

# THE SECTION LIST IS OURS, NOT AN st.radio, and that is the whole point.
#
# It was a radio, and a script reached into the sidebar to find Base Web's own
# circle, hide it, and draw a copy that followed the scroll. That worked on one
# story and failed on the others, then worked and left three rows with no
# circle at all. The failures were never the same twice because they were races
# against Streamlit: each rerun builds a new component iframe, timers scheduled
# on the parent window outlive the iframe that set them, and an orphaned script
# would re-hide markers after the current one had cleaned up. Nothing about that
# is fixable from outside; the DOM belongs to Streamlit and it is entitled to
# rebuild it whenever it likes.
#
# So the list is now markup this file owns. It still looks like a radio, it
# still marks one section, and the ring moves as you scroll. The difference is
# that every element the script touches was created here, so there is nothing
# to reverse-engineer and nothing to race.
#
# What is given up: it is no longer a widget, so clicking a row does not set
# session state. It does not need to. Clicking scrolls, which is all it was
# ever asked to do, and the hand-over from Diagnose still arrives through
# section_choice and still opens on the right section.
_TOC_CSS = """
<style>
.f1-toc { display: flex; flex-direction: column; gap: 0.42rem;
          margin: 0.25rem 0 0.5rem 0; }
.f1-toc a { display: flex; align-items: center; gap: 0.55rem;
            text-decoration: none; color: inherit; font-size: 0.875rem;
            line-height: 1.35; }
.f1-toc a:hover { color: #E10600; }
.f1-ring { flex: 0 0 auto; width: 17px; height: 17px; border-radius: 50%;
           border: 1px solid rgba(130,130,140,0.55); box-sizing: border-box;
           position: relative; transition: border-color 150ms ease-out; }
.f1-toc a.f1-active .f1-ring { border: 2px solid #E10600; }
.f1-toc a.f1-active .f1-ring::after {
    content: ""; position: absolute; inset: 2.5px; border-radius: 50%;
    background: #E10600; }
.f1-toc a.f1-active { font-weight: 600; }
</style>
"""

_toc_items = "".join(
    '<a class="f1-sec{active}" href="#sec-{key}" data-sec="{key}">'
    '<span class="f1-ring"></span><span>{title}</span></a>'.format(
        key=key, title=title,
        active=" f1-active" if key == section else "")
    for key, title in section_pairs
)
st.sidebar.markdown("Section")
st.sidebar.markdown(_TOC_CSS + f'<nav class="f1-toc">{_toc_items}</nav>',
                    unsafe_allow_html=True)

st.caption("Every section of this story is below. The sidebar follows as you "
           "scroll, and clicking a section jumps to it.")


def render_story(render_one) -> None:
    """
    Each section in the story's order, behind an invisible scroll target.

    NO HEADING IS ADDED HERE. Every block already opens with its own subheader,
    so adding one printed all 31 titles twice, in two sizes, disagreeing on
    punctuation. The anchor is an empty div instead: it gives the sidebar
    something to scroll to and puts nothing on the page.
    """
    for key, _title in section_pairs:
        st.divider()
        st.markdown(f'<div id="sec-{key}"></div>', unsafe_allow_html=True)
        render_one(key)


if story == "Story of a Race":
    render_story(lambda k: story_race.render(race, k))

elif story == "Story of a Driver":
    # The driver list depends on the race, so it is built here rather than
    # alongside the season and race pickers above.
    entrants = query("""
        SELECT f.driver_number, d.full_name, d.name_acronym, f.team_name,
               f.finish_position
        FROM fact_driver_race f
        JOIN dim_race r ON r.session_key = f.session_key
        LEFT JOIN dim_driver d
               ON d.driver_number = f.driver_number AND d.year = r.year
        WHERE f.session_key = ?
        ORDER BY f.finish_position IS NULL, f.finish_position
    """, (int(race.session_key),))

    if entrants.empty:
        st.info("No entrants recorded for this race.")
    else:
        driver_labels = {
            int(r.driver_number):
                f"{r.full_name or ('#' + str(int(r.driver_number)))}"
                f"  ·  {r.team_name}"
            for r in entrants.itertuples()
        }
        driver_options = list(driver_labels)

        # Changing race leaves the previous race's driver selected, who may
        # not have entered this one. Reset before the widget renders.
        if st.session_state.get("driver_choice") not in driver_options:
            st.session_state["driver_choice"] = driver_options[0]

        driver_number = st.sidebar.selectbox(
            "Driver", driver_options,
            format_func=lambda k: driver_labels.get(k, str(k)),
            key="driver_choice",
        )
        if story_driver.intro(race, driver_number):
            render_story(lambda k: story_driver.render(race, driver_number, k))

else:
    teams = query("""
        SELECT DISTINCT team_name FROM fact_driver_race
        WHERE session_key = ? ORDER BY team_name
    """, (int(race.session_key),))

    if teams.empty:
        st.info("No teams recorded for this race.")
    else:
        team_options = teams.team_name.tolist()

        # Changing race can leave a team selected that did not enter this one
        # (Cadillac joined in 2026). Reset before the widget renders.
        if st.session_state.get("team_choice") not in team_options:
            st.session_state["team_choice"] = team_options[0]

        team = st.sidebar.selectbox("Team", team_options, key="team_choice")
        if story_team.intro(race, team):
            render_story(lambda k: story_team.render(race, team, k))

st.sidebar.divider()
st.sidebar.caption(
    "Data from the OpenF1 API, rebuilt weekly. Clean laps exclude safety car, "
    "VSC and red flag periods."
)


# --- the radio and the page follow each other ------------------------------------
# Two behaviours, one script, both JavaScript because a Streamlit radio has no
# native way to move the page and no native way to notice that the page moved.
#
#   clicking the radio  scrolls to that section
#   scrolling the page  moves the radio's red ring to match
#   a button, low right  returns to the top
#
# THE RING IS A CSS CLASS ON OUR OWN ANCHORS. The script's whole job is to work
# out which section is on screen and move one class name. It reads no Streamlit
# markup and writes to no Streamlit element, so there is nothing to leave in a
# broken state and nothing to break on an upgrade.
#
# It still reaches into the host document, because the sidebar and the page are
# outside the component iframe. Same origin, so the browser allows it. The one
# exception to "only our own elements" is scroller(), which walks up from a
# section anchor reading overflow until it finds whatever Streamlit is actually
# scrolling. That is a read, and it has a fallback.
#
# If any of it fails, the list simply stops following the scroll. The sections
# are all still on the page, the links still jump, and the page still scrolls.
_scroll_to = None
if st.session_state.get("_scrolled_to") != (story, section):
    st.session_state["_scrolled_to"] = (story, section)
    _scroll_to = f"sec-{section}"

# Placeholders rather than an f-string, so the JavaScript can be written as
# JavaScript. Doubling every brace across a script this long is a silent
# correctness risk: a mis-escape is valid Python and broken JavaScript.
#
# EVERY ELEMENT THIS TOUCHES WAS CREATED BY THIS FILE. The rows are the
# .f1-sec anchors rendered above, the targets are the sec-* divs rendered by
# render_story, and the button is created here. It reads nothing of
# Streamlit's own markup, so a Streamlit upgrade cannot silently break it, and
# there is no state of Streamlit's to leave behind in a bad way. The worst case
# is the class never moves and the list simply sits still.
_TRACKER_JS = """
<script>
(function () {
  const doc = window.parent.document;
  const scrollTo = __SCROLL__;

  try {
    if (doc.__f1Cleanup) doc.__f1Cleanup();

    if (scrollTo) {
      const target = doc.getElementById(scrollTo);
      if (target) target.scrollIntoView({behavior: "smooth", block: "start"});
    }

    function links() {
      return Array.prototype.slice.call(doc.querySelectorAll("a.f1-sec"));
    }

    function targets() {
      return links().map(function (a) {
        return {link: a, anchor: doc.getElementById("sec-" + a.dataset.sec)};
      }).filter(function (r) { return !!r.anchor; });
    }

    // Whatever actually scrolls. Streamlit scrolls an inner div on some
    // versions and the document on others, so this asks rather than assumes.
    function scroller() {
      const rows = targets();
      let el = rows.length ? rows[0].anchor.parentElement : null;
      while (el && el !== doc.body) {
        const oy = getComputedStyle(el).overflowY;
        if ((oy === "auto" || oy === "scroll")
            && el.scrollHeight > el.clientHeight + 4) return el;
        el = el.parentElement;
      }
      return doc.scrollingElement || doc.documentElement;
    }

    let last = null;
    function update() {
      const rows = targets();
      if (!rows.length) return;

      // The section you are "in" is the last one whose anchor has passed the
      // top of the viewport. Anything else jitters at section boundaries.
      let best = rows[0];
      for (const r of rows) {
        if (r.anchor.getBoundingClientRect().top <= 120) best = r;
      }
      if (best.link === last) return;
      last = best.link;
      // Toggling one class on our own anchors. No Streamlit element is read
      // or modified, so nothing here can be left in a broken state.
      for (const r of rows) r.link.classList.remove("f1-active");
      best.link.classList.add("f1-active");
    }

    const toTop = doc.createElement("button");
    toTop.setAttribute("data-f1-top", "1");
    toTop.setAttribute("aria-label", "Back to top");
    toTop.title = "Back to top";
    Object.assign(toTop.style, {
      position: "fixed", right: "24px", bottom: "24px",
      width: "44px", height: "44px", borderRadius: "50%",
      border: "none", background: "#E10600", color: "#FFFFFF",
      cursor: "pointer", zIndex: "999999", opacity: "0",
      pointerEvents: "none", display: "flex", alignItems: "center",
      justifyContent: "center", boxShadow: "0 2px 8px rgba(0,0,0,0.28)",
      transition: "opacity 180ms ease-out",
    });
    // An SVG chevron rather than a character, so no platform can decide to
    // render it as an emoji.
    toTop.innerHTML =
      '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" '
      + 'stroke="currentColor" stroke-width="2.5" stroke-linecap="round" '
      + 'stroke-linejoin="round"><polyline points="18 15 12 9 6 15">'
      + '</polyline></svg>';
    toTop.addEventListener("click", function () {
      const s = scroller();
      if (s === doc.scrollingElement || s === doc.documentElement) {
        window.parent.scrollTo({top: 0, behavior: "smooth"});
      } else {
        s.scrollTo({top: 0, behavior: "smooth"});
      }
    });

    function updateButton() {
      const s = scroller();
      const y = (s === doc.scrollingElement || s === doc.documentElement)
                ? (window.parent.scrollY || s.scrollTop) : s.scrollTop;
      const show = y > 400;
      toTop.style.opacity = show ? "1" : "0";
      toTop.style.pointerEvents = show ? "auto" : "none";
    }

    // Old buttons from a previous run go before this one is added, so a rerun
    // whose cleanup was missed cannot stack them up.
    doc.querySelectorAll("[data-f1-top]").forEach(function (n) {
      if (n.parentNode) n.parentNode.removeChild(n);
    });
    doc.body.appendChild(toTop);

    // No throttling and no animation frame. update() reads a handful of
    // rectangles and writes nothing unless the active row actually changed,
    // and the browser already caps scroll events at one per frame.
    //
    // Deliberately NOT window.parent.requestAnimationFrame or setTimeout: a
    // callback scheduled on the parent window outlives the iframe that
    // scheduled it, which is precisely how an orphaned script from an earlier
    // rerun stayed alive and fought the current one.
    function onScroll() {
      update();
      updateButton();
    }

    // capture:true because a scroll event on an inner container does not
    // bubble, and Streamlit scrolls a div rather than the document.
    window.parent.addEventListener("scroll", onScroll, true);
    window.parent.addEventListener("resize", onScroll);

    doc.__f1Cleanup = function () {
      window.parent.removeEventListener("scroll", onScroll, true);
      window.parent.removeEventListener("resize", onScroll);
      if (toTop.parentNode) toTop.parentNode.removeChild(toTop);
    };

    update();
    updateButton();
  } catch (err) {
    console.error("F1 section tracker failed:", err);
  }
})();
</script>
"""

components.html(_TRACKER_JS.replace("__SCROLL__", json.dumps(_scroll_to)),
                height=0)

render_footer()
