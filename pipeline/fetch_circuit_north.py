"""
fetch_circuit_north.py - where north is, per circuit.

    python pipeline\\fetch_circuit_north.py                          # report, write nothing
    python pipeline\\fetch_circuit_north.py --execute                # refresh circuit_north.json
    python pipeline\\fetch_circuit_north.py --missing-only           # report on circuits with no rotation
    python pipeline\\fetch_circuit_north.py --missing-only --execute # add them, only if they verify
    python pipeline\\fetch_circuit_north.py --verify-known           # re-check every stored rotation

WHY THIS EXISTS
---------------
OpenF1 reports car positions in each circuit's own coordinate frame and never
says how that frame is turned relative to north. There is no latitude, longitude
or bearing anywhere in bronze, silver or gold: 59 tables, checked. So the track
map could draw the shape of a circuit and the direction of travel, but could not
honestly say which way was north. s05b_prescriptive.add_wind_components reaches
the same conclusion from the other end, which is why wind enters that model as
two components crossed with circuit rather than as a bearing.

The missing number was already reachable. silver_meetings stores a
circuit_info_url per meeting, pointing at the MultiViewer circuits API, and that
response carries a top-level `rotation`. Nobody had ever fetched it.

WHY THE WEEKLY RUN CALLS IT, AND ONLY NARROWLY
----------------------------------------------
This was a hand-run script, for a reason that still holds: a circuit's
orientation does not change, and the weekly run should not depend on somebody
else's server for a constant. A rotation already stored is never fetched again.

What that did not cover is a circuit new to the calendar. Madring was raced on
13 September 2026, got its map on the next run, and had a compass with no
letters, because nothing ever asked MultiViewer about it. So run_pipeline.py now
calls --missing-only after the map is built. It asks ONLY about circuits that
have a traced outline and no rotation, it never fails the run, and it adds a
rotation only when it passes every check below. On the day it was written that
was one request per run, for Madring, which MultiViewer answered with a 404 at
every address tried: it had no record of the circuit at all.

WHAT IS CHECKED BEFORE A NUMBER IS ACCEPTED
-------------------------------------------
A rotation from an outside source is worth nothing until it is shown to describe
THIS project's coordinates. Three checks:

  every circuit returns one     92 of 94 circuit-year URLs; the 2 failures are
                                404s for circuits with no outline here anyway
  stable across years           0 of 24 circuits disagreed with themselves
  same coordinate frame         MultiViewer's track shape lined up against the
                                outline this project traced from a real lap

The third check was first done ONCE, BY HAND, which is why the script could not
be trusted with a new circuit: it would have written whatever number came back.
It is now verify_against_outline, and nothing reaches circuit_north.json without
passing it. Both shapes are resampled evenly along the track, centred, scaled,
and compared at every starting point in both directions of travel, then again
with MultiViewer's shape mirrored. Calibrated 2026-09-14 on the 24 circuits the
hand check had already accepted:

  measure                         measured on the 24     accepted when
  turn needed to line up          at most 0.41 deg       at most 3 deg
  remaining misfit                at most 0.0195         at most 0.06
  mirrored fit, times worse       at least 11.4x         at least 5x
  MultiViewer size against ours   9.98x to 10.01x        9.5x to 10.5x

3 degrees is the bound the hand check used. The other limits leave between two
and three times the worst value measured, so an ordinary circuit clears them and
a wrong frame, a mirrored one or a different track does not. They are a
calibration, not a law, and a circuit rejected by one of them is reported with
its numbers rather than silently dropped.

THE ONE THING STILL OPEN
------------------------
The API does not state its convention. `rotation` is either the angle the map
must be turned to put north up, or the bearing north already sits at. Those
differ by a sign. NORTH_CONVENTION in dashboard/race_map.py holds the choice, so
settling it is a one-line change rather than 24.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import DB_PATH  # noqa: E402
import serving  # noqa: E402

OUT_PATH = Path(__file__).resolve().parent / "circuit_north.json"
PAUSE_SECONDS = 0.25          # unhurried; it is somebody else's server

# Verification limits. See "WHAT IS CHECKED" above for where each came from.
MAX_TURN_DEGREES = 3.0
MAX_MISFIT = 0.06
MIN_MIRROR_RATIO = 5.0
SCALE_RANGE = (9.5, 10.5)
RESAMPLE_POINTS = 400


def wanted(con) -> list[tuple]:
    """One URL per circuit-year that this project actually raced at."""
    return con.execute("""
        SELECT DISTINCT circuit_key, circuit_short_name, year, circuit_info_url
        FROM silver_meetings
        WHERE circuit_info_url IS NOT NULL AND circuit_info_url != ''
          AND is_cancelled = 0
        ORDER BY circuit_key, year
    """).fetchall()


def fetch_circuit(url: str) -> tuple[dict | None, str | None]:
    """One MultiViewer circuit record, or (None, reason). Never raises."""
    try:
        r = requests.get(url, timeout=30,
                         headers={"User-Agent": "F1-Reality-Check/1.0"})
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}"
        return r.json(), None
    except Exception as e:  # noqa: BLE001
        return None, type(e).__name__
    finally:
        time.sleep(PAUSE_SECONDS)


def load_outlines() -> dict[int, np.ndarray]:
    """This project's traced outline per circuit, from the bundle s05c writes."""
    if not serving.BUNDLE_DB.exists():
        return {}
    con = duckdb.connect(str(serving.BUNDLE_DB), read_only=True)
    try:
        tables = {r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables").fetchall()}
        if "map_circuit_outline" not in tables:
            return {}
        df = con.execute("SELECT circuit_key, x, y FROM map_circuit_outline "
                         "ORDER BY circuit_key, seq").df()
    finally:
        con.close()
    return {int(k): g[["x", "y"]].to_numpy(dtype=float)
            for k, g in df.groupby("circuit_key")}


# --- verification ------------------------------------------------------------------

def _resample_loop(xy: np.ndarray, n: int = RESAMPLE_POINTS) -> np.ndarray:
    """n points evenly spaced by distance along a closed loop."""
    closed = np.vstack([xy, xy[:1]])
    seg = np.hypot(*np.diff(closed, axis=0).T)
    closed = closed[np.concatenate([[True], seg > 0])]      # drop repeated points
    seg = np.hypot(*np.diff(closed, axis=0).T)
    dist = np.concatenate([[0.0], np.cumsum(seg)])
    targets = np.linspace(0.0, dist[-1], n, endpoint=False)
    return np.column_stack([np.interp(targets, dist, closed[:, 0]),
                            np.interp(targets, dist, closed[:, 1])])


def _normalise(xy: np.ndarray) -> tuple[np.ndarray, float]:
    """Centred, and scaled to unit RMS radius, so units need not match."""
    centred = xy - xy.mean(axis=0)
    scale = float(np.sqrt((centred ** 2).sum(axis=1).mean()))
    return centred / scale, scale


def _best_rotation(ours: np.ndarray, theirs: np.ndarray) -> tuple[float, float]:
    """
    The rotation of `theirs` that best matches `ours`, over every starting point
    and both directions of travel. Returns (degrees, RMS misfit) at the best fit.
    """
    best = (0.0, float("inf"))
    ax, ay = ours[:, 0], ours[:, 1]
    for candidate in (theirs, theirs[::-1]):
        for offset in range(len(candidate)):
            b = np.roll(candidate, -offset, axis=0)
            bx, by = b[:, 0], b[:, 1]
            theta = np.arctan2((bx * ay - by * ax).sum(), (bx * ax + by * ay).sum())
            c, s = np.cos(theta), np.sin(theta)
            misfit = float(np.sqrt(((ax - (c * bx - s * by)) ** 2
                                    + (ay - (s * bx + c * by)) ** 2).mean()))
            if misfit < best[1]:
                best = (float(np.degrees(theta)), misfit)
    return best


def verify_against_outline(record: dict, outline: np.ndarray) -> tuple[bool, dict, str]:
    """
    Does MultiViewer's track shape sit in the same frame as this project's?

    Returns (passed, the four measurements, what failed). A shape that is
    missing or too short to compare fails rather than passing by default.
    """
    x, y = record.get("x"), record.get("y")
    if not x or not y or len(x) != len(y) or len(x) < 50:
        return False, {}, "no usable track shape in the response"

    theirs_raw = np.column_stack([np.asarray(x, dtype=float),
                                  np.asarray(y, dtype=float)])
    ours, s_ours = _normalise(_resample_loop(outline))
    theirs, s_theirs = _normalise(_resample_loop(theirs_raw))
    mirrored, _ = _normalise(_resample_loop(theirs_raw * np.array([-1.0, 1.0])))

    turn, misfit = _best_rotation(ours, theirs)
    _, mirror_misfit = _best_rotation(ours, mirrored)
    ratio = min(mirror_misfit / misfit, 9999.0) if misfit > 0 else 9999.0
    scale = s_theirs / s_ours

    metrics = {"turn_degrees": round(turn, 2), "misfit": round(misfit, 4),
               "mirror_ratio": round(ratio, 1), "scale": round(scale, 2)}
    problems = []
    if abs(turn) > MAX_TURN_DEGREES:
        problems.append(f"needs {turn:.1f} deg of turn to line up")
    if misfit > MAX_MISFIT:
        problems.append(f"misfit {misfit:.3f} after lining up")
    if ratio < MIN_MIRROR_RATIO:
        problems.append(f"mirrored fit only {ratio:.1f}x worse")
    if not SCALE_RANGE[0] <= scale <= SCALE_RANGE[1]:
        problems.append(f"scale {scale:.2f}x ours")
    return not problems, metrics, "; ".join(problems)


def _entry(ckey: int, name: str, record: dict, url: str, metrics: dict) -> dict:
    return {
        "circuit_key": ckey,
        "circuit_short_name": name,
        "rotation": record["rotation"],
        "source_year": record.get("year"),
        "source_url": url,
        "verified": metrics,
    }


# --- modes -------------------------------------------------------------------------

def refresh_all(execute: bool) -> int:
    """Re-fetch every circuit. Writes only if every circuit with an outline verifies."""
    con = duckdb.connect(str(DB_PATH), read_only=True)
    rows = wanted(con)
    con.close()
    outlines = load_outlines()
    print(f"{len(rows)} circuit-year URLs to check\n")

    seen: dict[int, dict] = {}
    records: dict[int, tuple[dict, str, str]] = {}
    conflicts: list[str] = []
    failures: list[str] = []

    for i, (ckey, name, year, url) in enumerate(rows, 1):
        data, why = fetch_circuit(url)
        if data is None:
            failures.append(f"{name} {year}: {why}")
            continue

        rot = data.get("rotation")
        if rot is None:
            failures.append(f"{name} {year}: no rotation field")
            continue

        prev = seen.get(ckey)
        if prev and prev["rotation"] != rot:
            # A circuit disagreeing with itself means the number describes
            # something that changes, and it cannot be treated as a constant.
            conflicts.append(
                f"{name}: {prev['rotation']} ({prev['source_year']}) "
                f"vs {rot} ({data.get('year')})")
        elif not prev:
            seen[ckey] = {"rotation": rot, "source_year": data.get("year")}
            records[ckey] = (data, name, url)
        if i % 20 == 0:
            print(f"  {i}/{len(rows)}")

    print(f"\ncircuits with a rotation : {len(seen)}")
    print(f"requests that failed     : {len(failures)}")
    for f in failures:
        print(f"    {f}")
    print(f"circuits disagreeing with themselves: {len(conflicts)}")
    for c in conflicts:
        print(f"    {c}")
    if conflicts:
        print("\n[FAIL] a rotation that changes is not a constant. Not written.")
        return 1

    verified, rejected, unverifiable = [], [], []
    for ckey in sorted(records):
        data, name, url = records[ckey]
        if ckey not in outlines:
            unverifiable.append(name)
            continue
        passed, metrics, reason = verify_against_outline(data, outlines[ckey])
        if passed:
            verified.append(_entry(ckey, name, data, url, metrics))
        else:
            rejected.append(f"{name}: {reason} {metrics}")

    print(f"\nverified against this project's outline: {len(verified)}")
    if unverifiable:
        print(f"no traced outline to verify against, left out: {', '.join(unverifiable)}")
    if rejected:
        for r in rejected:
            print(f"    REJECTED {r}")
        print("\n[FAIL] a rotation failed verification. Not written.")
        return 1

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "MultiViewer circuits API, via silver_meetings.circuit_info_url",
        "note": ("rotation is in the same coordinate frame as OpenF1 position "
                 "data; verified per circuit against this project's own traced "
                 "outline by fetch_circuit_north.verify_against_outline. The "
                 "convention (which way the angle turns) is set by "
                 "NORTH_CONVENTION in dashboard/race_map.py."),
        "circuits": verified,
    }

    if not execute:
        print(f"\nDRY RUN. Re-run with --execute to write {OUT_PATH.name}.")
        for c in verified:
            print(f"    {c['circuit_key']:>4} {c['circuit_short_name']:<20} "
                  f"{c['rotation']:>4}  {c['verified']}")
        return 0

    OUT_PATH.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    print(f"\nWrote {OUT_PATH.name}: {len(verified)} circuits.")
    return 0


def add_missing(execute: bool) -> int:
    """
    Add a rotation for each circuit that has a traced outline and none stored.

    Existing entries are never fetched, re-checked or rewritten. A circuit that
    is not available yet, or that fails verification, is reported and left for
    the next run. The last line is machine-read by run_pipeline.py.
    """
    existing = (json.loads(OUT_PATH.read_text(encoding="utf-8"))
                if OUT_PATH.exists() else {"circuits": []})
    have = {int(c["circuit_key"]) for c in existing["circuits"]}
    outlines = load_outlines()
    targets = sorted(k for k in outlines if k not in have)

    con = duckdb.connect(str(DB_PATH), read_only=True)
    rows = wanted(con)
    con.close()
    by_circuit: dict[int, list[tuple]] = {}
    for ckey, name, year, url in rows:
        by_circuit.setdefault(int(ckey), []).append((year, name, url))

    names = {k: (by_circuit[k][0][1] if k in by_circuit else f"circuit {k}")
             for k in targets}
    print(f"circuits with a map and no rotation: {len(targets)}"
          + (f" ({', '.join(names[k] for k in targets)})" if targets else ""))

    added, still_missing = [], []
    for ckey in targets:
        name = names[ckey]
        urls = sorted(by_circuit.get(ckey, []), reverse=True)     # newest first
        if not urls:
            print(f"  {name}: no circuit_info_url in silver_meetings")
            still_missing.append(ckey)
            continue

        record, why, used = None, None, None
        for _, _, url in urls:
            record, why = fetch_circuit(url)
            if record is not None:
                used = url
                break
        if record is None:
            print(f"  {name}: not available from MultiViewer yet ({why})")
            still_missing.append(ckey)
            continue
        if record.get("rotation") is None:
            print(f"  {name}: MultiViewer response has no rotation")
            still_missing.append(ckey)
            continue

        passed, metrics, reason = verify_against_outline(record, outlines[ckey])
        if not passed:
            print(f"  {name}: rotation {record['rotation']} FAILED verification, "
                  f"not added: {reason} {metrics}")
            still_missing.append(ckey)
            continue
        print(f"  {name}: rotation {record['rotation']} verified {metrics}")
        added.append(_entry(ckey, name, record, used, metrics))

    if added and execute:
        payload = dict(existing)
        payload["circuits"] = sorted(existing["circuits"] + added,
                                     key=lambda c: int(c["circuit_key"]))
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        OUT_PATH.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
        print(f"Wrote {OUT_PATH.name}: added {len(added)}, "
              f"{len(payload['circuits'])} circuits in total.")
    elif added:
        print("DRY RUN. Re-run with --execute to add them.")

    print(f"north missing: {still_missing}")
    print(f"north added: {[e['circuit_key'] for e in added] if execute else []}")
    return 0


def verify_known() -> int:
    """Re-check every stored rotation against the traced outlines. Writes nothing."""
    existing = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    outlines = load_outlines()
    bad = 0
    for c in existing["circuits"]:
        ckey, name = int(c["circuit_key"]), c["circuit_short_name"]
        record, why = fetch_circuit(c["source_url"])
        if record is None:
            print(f"  {name:<20} could not fetch ({why})")
            bad += 1
            continue
        if ckey not in outlines:
            print(f"  {name:<20} no traced outline to verify against")
            bad += 1
            continue
        passed, metrics, reason = verify_against_outline(record, outlines[ckey])
        same = record.get("rotation") == c["rotation"]
        status = "PASS" if passed and same else "FAIL"
        bad += status == "FAIL"
        note = "" if same else f"  rotation now {record.get('rotation')}, stored {c['rotation']}"
        print(f"  {name:<20} {status}  {metrics}{('  ' + reason) if reason else ''}{note}")
    print(f"\n{len(existing['circuits']) - bad} of {len(existing['circuits'])} verified")
    return 0 if bad == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch and verify circuit north rotations.")
    ap.add_argument("--execute", action="store_true",
                    help="write circuit_north.json; without it, report only")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--missing-only", action="store_true",
                      help="only circuits with a traced outline and no rotation; "
                           "existing entries are never touched")
    mode.add_argument("--verify-known", action="store_true",
                      help="re-check every stored rotation, writing nothing")
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"[FAIL] silver database not found at {DB_PATH}")
        return 1

    if args.verify_known:
        return verify_known()
    if args.missing_only:
        return add_missing(args.execute)
    return refresh_all(args.execute)


if __name__ == "__main__":
    raise SystemExit(main())
