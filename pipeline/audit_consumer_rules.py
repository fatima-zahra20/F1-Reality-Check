"""
audit_consumer_rules.py — where does the analysis population get defined?

Read-only. Writes nothing, opens no database. Safe to run any time.

WHY THIS EXISTS
---------------
Gold is meant to hold one processed, conformed view that every analytical,
diagnostic and predictive question reads from. Its specification is not a design
problem: it already exists, scattered across the codebase as filters each
consumer wrote for itself. This finds them.

The premise is that a decision like "which laps count as racing laps" is a
property of the data, not of whoever happens to be querying it. Wherever such a
decision is made at a call site, it can differ from the same decision made
elsewhere, and nothing will ever report the divergence.

HOW TO READ THE OUTPUT
----------------------
Two kinds of decision, and they mean different things:

  conflict   variants are competing answers to ONE question. Every extra
             variant is a place the project silently disagrees with itself.
  spread     one rule, or legitimately different scopes for different
             questions. The count is a measure of duplication, not of conflict.

Confusing the two overstates the problem, so they are tallied separately.

Sources are weighted, because they do not carry equal authority:

  pipeline   produces data others depend on. A variant here is load-bearing.
  consumer   dashboard and data_prep. Reads data; should not define truth.
  notebook   the diagnostic analyses. Their variants ARE the stated method.
  sql        the descriptive query library.
  explore    profiling and EDA. Throwaway by nature: listed, never counted.

USE
---
    python pipeline\\audit_consumer_rules.py

Re-run it as gold absorbs each rule. Site counts should fall toward one, and a
decision that reaches "single-sourced" has been genuinely centralised rather
than merely intended to be.

See GOLD_INVENTORY.md for the findings this produced on 2026-08-11, and the
decisions still open.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Sources whose variants count. 'explore' is deliberately absent, and so is
# 'gold'.
#
# WHY 'gold' IS NOT WEIGHTED. This audit counts places where a decision is
# RE-DECIDED. The gold builder is where a decision is decided ONCE, on purpose,
# and every other site is supposed to read the result. Counting it as a variant
# made building gold look like a regression: the first run after gold_lap landed
# reported phantom stints going from 1 variant to 2 and stop_duration scope from
# 2 to 3, purely because the authoritative definition now exists in a file.
#
# So gold is reported separately, as the owner. A decision defined in gold with
# zero weighted sites left is the finished state this audit is measuring toward.
WEIGHTED = {"pipeline", "consumer", "notebook", "sql"}
GOLD_FILES = {"s07_build_gold.py"}

SCAN = [
    "*.py", "pipeline/*.py", "dashboard/*.py", "dashboard/**/*.py",
    "DIAGNOSTIC ANALYTICS/*.ipynb", "DESCRIPTIVE ANALYTICS/*.sql",
    "DATA PROFILING/*.sql", "DATA PROFILING/*.ipynb",
]


def classify(path: Path) -> str:
    parts = {p.lower() for p in path.parts}
    if "data profiling" in parts or path.name.startswith("EDA_"):
        return "explore"
    if path.name in GOLD_FILES:
        return "gold"
    if "pipeline" in parts:
        return "pipeline"
    if "diagnostic analytics" in parts:
        return "notebook"
    if "descriptive analytics" in parts:
        return "sql"
    if "dashboard" in parts or path.name == "data_prep.py":
        return "consumer"
    return "other"


def scan_lines():
    """Yields (relative_path, tag, location, line) for everything scannable."""
    seen: set[Path] = set()
    # This file describes every pattern it searches for, so scanning itself
    # produces a match for each one and reports the audit as an enforcement
    # site. Found the first time it ran: n_gear appeared as "single-sourced in
    # pipeline" when the only hit was this docstring.
    me = Path(__file__).resolve()
    for pattern in SCAN:
        for path in PROJECT_ROOT.glob(pattern):
            if (".ipynb_checkpoints" in str(path) or path in seen
                    or path.resolve() == me):
                continue
            seen.add(path)
            tag = classify(path)
            rel = path.relative_to(PROJECT_ROOT)

            if path.suffix == ".ipynb":
                # Notebook source is JSON-escaped, so the raw file cannot be
                # line-matched directly without false positives from output.
                try:
                    nb = json.loads(path.read_text(encoding="utf-8"))
                except (ValueError, OSError):
                    continue
                index = -1
                for cell in nb.get("cells", []):
                    if cell.get("cell_type") != "code":
                        continue
                    index += 1
                    source = "".join(cell.get("source", []))
                    for n, line in enumerate(source.splitlines(), 1):
                        yield rel, tag, f"cell {index}:{n}", line
            else:
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                for n, line in enumerate(text.splitlines(), 1):
                    yield rel, tag, str(n), line


DECISIONS: list[tuple[str, str, str, object]] = []

# Which gold column owns each decision, named explicitly rather than inferred.
#
# Inferring ownership from the same regexes that find call sites does not work,
# and reported the wrong thing once. Gold owns "valid racing lap" precisely by
# NOT containing a duration window, so a pattern hunting for `BETWEEN 60 AND
# 200` can never find it there. Same for team names: gold conforms them through
# its own helper, not through `normalize_team_names`.
#
# An entry here is a claim that the column exists and is authoritative. The
# check below verifies the identifier is actually present in the gold builder,
# so a renamed or deleted column shows up as unowned instead of silently
# continuing to look finished.
GOLD_OWNER = {
    "valid racing lap": "is_representative_lap",
    "team name normalization": "TEAM_NAME_MAP",
    "race scoping": "session_name",
    "phantom stints": "is_phantom_stint",
    "pit duration outliers": "is_green_race_stop",
    "stop_duration year scope": "has_stop_duration",
    "neutralisation flags": "neutralised",
    "future calendar": "has_laps",
    # Deliberately unowned so far, and named here so the gap stays visible:
    #   excluded teams  -> still 3 competing rules across 19 sites
    #   corrupt n_gear  -> lives in car_data, which is bronze only
}


def decision(name: str, kind: str, question: str):
    """Registers a matcher. kind is 'conflict' or 'spread'."""
    def wrap(fn):
        DECISIONS.append((name, kind, question, fn))
        return fn
    return wrap


@decision("valid racing lap", "conflict",
          "Which laps count as representative racing laps?")
def _lap(line: str):
    if "lap_duration" not in line:
        return None
    m = re.search(r"lap_duration.{0,20}?BETWEEN\s*(\d+)\s*AND\s*(\d+)", line, re.I)
    if m:
        return f"bounds {m.group(1)}-{m.group(2)}"
    m = re.search(r"lap_duration.{0,12}?\.between\(\s*(\d+)\s*,\s*(\d+)", line, re.I)
    if m:
        return f"bounds {m.group(1)}-{m.group(2)}"
    m = re.search(r"lap_duration'?\]?\s*(<=|<|>=|>)\s*([\d.]+)\s*\*\s*(\w+)", line)
    if m:
        return f"relative {m.group(1)} {m.group(2)} x {m.group(3)}"
    m = re.search(r"lap_duration'?\]?\s*(<=|<|>=|>)\s*(\d+)", line)
    if m:
        return f"single bound {m.group(1)} {m.group(2)}"
    if re.search(r"lap_duration.{0,30}(fence|quantile|iqr)", line, re.I):
        return "statistical fence"
    return None


@decision("team name normalization", "spread",
          "Are team names conformed before grouping across seasons?")
def _team(line: str):
    if "normalize_team_names" not in line:
        return None
    if re.search(r"^\s*(def|import|from)\s", line):
        return "definition or import"
    return "applied at call site"


@decision("race scoping", "spread",
          "Which sessions are 'a race'? (different scopes are legitimate; the "
          "count measures how often the join is rewritten)")
def _race(line: str):
    m = re.search(r"session_name\s*(?:=|==|IN)\s*\(?\s*'([^']+)'"
                  r"(?:\s*,\s*'([^']+)')?", line, re.I)
    if not m:
        return None
    return " + ".join(sorted(g for g in m.groups() if g))


@decision("phantom stints", "conflict",
          "Are stints with lap_end < lap_start excluded? "
          "(DATA_DICTIONARY says filter in gold)")
def _stint(line: str):
    m = re.search(r"lap_end\s*(>=|<|<=|>)\s*lap_start", line)
    return m.group(0) if m else None


@decision("corrupt n_gear", "conflict",
          "Are the ~600 rows with n_gear > 8 excluded? "
          "(DATA_DICTIONARY says filter in gold)")
def _gear(line: str):
    m = re.search(r"n_gear\s*(<=|<|>=|>|==)\s*(\d+)", line)
    return m.group(0) if m else None


@decision("pit duration outliers", "conflict",
          "Are red-flag pit durations (up to 16,921s) excluded? "
          "(DATA_DICTIONARY says filter in gold)")
def _pit(line: str):
    m = re.search(r"(pit_duration|lane_duration|stop_duration)\s*"
                  r"(?:BETWEEN\s*[\d.]+\s*AND\s*[\d.]+|(?:<=|<|>=|>)\s*[\d.]+)",
                  line, re.I)
    return m.group(0).strip() if m else None


@decision("excluded teams", "conflict",
          "Is Cadillac excluded, and is that enforced from one place?")
def _cadillac(line: str):
    if "EXCLUDED_TEAMS" in line:
        return ("constant DEFINED" if re.match(r"\s*EXCLUDED_TEAMS\s*=", line)
                else "constant used")
    if re.search(r"['\"]Cadillac", line):
        return "hardcoded literal"
    return None


@decision("stop_duration year scope", "conflict",
          "Is stop_duration scoped to 2024+, given zero coverage in 2023?")
def _stopyear(line: str):
    if "STOP_DURATION_MIN_YEAR" in line:
        return ("constant DEFINED"
                if re.match(r"\s*STOP_DURATION_MIN_YEAR\s*=", line)
                else "constant used")
    if re.search(r"year\s*(>=|>)\s*202[34]", line):
        return "hardcoded year"
    return None


@decision("future calendar", "spread",
          "How is the unrun future calendar excluded?")
def _future(line: str):
    if not re.search(r"date_start\s*<", line, re.I):
        return None
    grace = re.search(r"-(\d+)\s*day", line)
    return (f"date_start < now minus {grace.group(1)} days" if grace
            else "date_start < now")


@decision("neutralisation flags", "spread",
          "How is a caution lap identified? THE PATTERN GOLD SHOULD COPY: "
          "materialized once, read everywhere")
def _neutral(line: str):
    if "silver_lap_flags" in line:
        return "materialized in silver_lap_flags"
    for token in ("neutralised", "sc_flag", "vsc_flag", "red_flag",
                  "yellow_sector_flag"):
        if token in line:
            return f"reads {token}"
    if "caution_flag" in line:
        return "caution_flag (superseded)"
    return None


def main() -> int:
    lines = list(scan_lines())
    files = len({p for p, _, _, _ in lines})
    print("=" * 78)
    print("CONSUMER RULE AUDIT — where the analysis population is defined")
    print(f"scanned {files} files, {len(lines):,} lines")
    print("=" * 78)

    findings: dict = defaultdict(lambda: defaultdict(list))
    for path, tag, loc, line in lines:
        for name, _kind, _q, fn in DECISIONS:
            variant = fn(line)
            if variant:
                findings[name][variant].append((tag, str(path), loc,
                                                line.strip()))

    for name, kind, question, _fn in DECISIONS:
        variants = findings[name]
        real = {v: h for v, h in variants.items()
                if any(t in WEIGHTED for t, *_ in h)}
        sites = sum(len([x for x in h if x[0] in WEIGHTED])
                    for h in real.values())

        print("\n" + "=" * 78)
        print(f"{name.upper()}   [{kind}]")
        print(f"  {question}")
        print("-" * 78)
        if not real:
            print("  NOT ENFORCED in any weighted source.")
            if variants:
                print(f"  (appears only in exploratory files: "
                      f"{list(variants)})")
            continue

        print(f"  {len(real)} variant(s), {sites} site(s)")
        for variant, hits in sorted(variants.items(), key=lambda kv: -len(kv[1])):
            tags: dict = defaultdict(int)
            for tag, *_ in hits:
                tags[tag] += 1
            note = "" if any(t in WEIGHTED for t, *_ in hits) else "  [explore only]"
            print(f"\n    {variant!r} -> {len(hits)} site(s) "
                  f"{dict(sorted(tags.items()))}{note}")
            for tag, path, loc, line in hits[:3]:
                print(f"        [{tag:8s}] {path}:{loc}")
                print(f"                   {line[:92]}")
            if len(hits) > 3:
                print(f"        ... and {len(hits) - 3} more")

    print("\n" + "=" * 78)
    print("SUMMARY (exploratory sources excluded)")
    print("=" * 78)
    gold_path = PROJECT_ROOT / "pipeline" / "s07_build_gold.py"
    gold_source = (gold_path.read_text(encoding="utf-8")
                   if gold_path.exists() else "")
    if not gold_source:
        print("  [note] pipeline/s07_build_gold.py not found; "
              "nothing can be reported as owned by gold")

    print(f"{'decision':28s} {'kind':9s} {'variants':>8} {'sites':>6} "
          f"{'gold':>5}  verdict")
    conflicts = 0
    owned = 0
    for name, kind, _q, _fn in DECISIONS:
        real = {v: h for v, h in findings[name].items()
                if any(x[0] in WEIGHTED for x in h)}
        sites = sum(len([x for x in h if x[0] in WEIGHTED])
                    for h in real.values())
        # Is this decision defined in the gold builder? That is the owner, not
        # another competing variant, so it is reported in its own column.
        # Verified against the builder's actual text so a renamed column drops
        # out of "owned" rather than staying there on the strength of a dict.
        col = GOLD_OWNER.get(name)
        in_gold = bool(col) and col in gold_source
        if in_gold:
            owned += 1

        if not real and not in_gold:
            verdict = "NOT ENFORCED"
        elif not real and in_gold:
            verdict = "OWNED BY GOLD, no call site left"
        elif kind == "conflict" and len(real) > 1:
            verdict = f"{len(real)} COMPETING RULES"
            if in_gold:
                verdict += " (gold owns it; migrate the rest)"
            conflicts += 1
        elif sites <= 2:
            verdict = "single-sourced"
        else:
            verdict = f"one rule, repeated {sites}x by hand"
            if in_gold:
                verdict += " (gold owns it; migrate the rest)"
        print(f"{name:28s} {kind:9s} {len(real):>8} {sites:>6} "
              f"{'yes' if in_gold else '-':>5}  {verdict}")

    print(f"\ndecisions in genuine conflict: {conflicts}")
    print(f"decisions now defined in gold:  {owned} of {len(DECISIONS)}")
    print("\nGoal: every decision OWNED BY GOLD with no call site left. A gold")
    print("column with call sites still beside it means the definition exists")
    print("but nothing reads it yet, which is progress and not completion.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
