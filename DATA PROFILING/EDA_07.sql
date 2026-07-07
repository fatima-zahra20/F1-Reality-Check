-- DATA PROFILING PHASE 7
-- Consistency 
-- Compare recorded finish laps vs actual laps in the laps table
SELECT COUNT(*) AS mismatch_rows
FROM silver_session_result sr
JOIN (
    SELECT session_key, driver_number, MAX(lap_number) AS actual_max_lap
    FROM silver_laps
    GROUP BY session_key, driver_number
) l ON sr.session_key = l.session_key AND sr.driver_number = l.driver_number
WHERE sr.number_of_laps IS NOT NULL 
  AND sr.number_of_laps != l.actual_max_lap;
-- 262 

-- Verdict on session_result vs laps consistency:
-- 
-- 262 rows have number_of_laps ≠ MAX(lap_number in silver_laps) (3.4% of 7660).
-- Breakdown:
--   - 187 DNF drivers: retired mid-lap, attempted lap logged but not counted.
--   - 15 DNS drivers with pre-race lap data (formation lap artifacts).
--   - 5 "NC" (Not Classified) drivers who finished without a position (already
--     documented in completeness verdict).
--   - 5 rows where result_laps = 0 despite 14-21 recorded laps: genuine data 
--     quality gap in OpenF1's classification feed for these specific rows.
--   - 3 rows with 3-6 lap discrepancies: late-race retirements or session 
--     shortening events.
--   - ~47 rows off by 1 lap: standard "attempted final lap" pattern.
--
-- For prediction model using session-level or team-level aggregates, this is
-- negligible noise (~1% row impact, mostly at margins).
-- 
-- If precise per-driver lap counts matter for a specific analysis, prefer 
-- MAX(lap_number) from silver_laps over session_result.number_of_laps — the 
-- laps table is more granular and typically more accurate.



SELECT sr.session_key, sr.driver_number, sr.number_of_laps AS result_laps, l.actual_max_lap
FROM silver_session_result sr
JOIN (
    SELECT session_key, driver_number, MAX(lap_number) AS actual_max_lap
    FROM silver_laps
    GROUP BY session_key, driver_number
) l ON sr.session_key = l.session_key AND sr.driver_number = l.driver_number
WHERE sr.number_of_laps IS NOT NULL 
  AND sr.number_of_laps != l.actual_max_lap
LIMIT 20;

-- The +1 mismatch pattern is a timing-sensor artifact. When a driver crosses 
-- the start/finish line to receive the checkered flag, the timing system logs 
-- a "new lap started" row even though no lap is completed after the race ends. 
-- These phantom rows have all NULL values (no sector times, no lap_duration). 
-- Filter WHERE lap_duration IS NOT NULL to exclude them, or use 
-- session_result.number_of_laps for the correctly-classified count.



-- For each Race session, get race distance = max lap_number across all drivers
-- Then check: DNF drivers should have completed FEWER laps than race distance
SELECT COUNT(*) AS suspicious_dnf_rows
FROM silver_session_result sr
JOIN silver_sessions s ON sr.session_key = s.session_key
JOIN (
    SELECT session_key, MAX(lap_number) AS race_distance
    FROM silver_laps
    GROUP BY session_key
) rd ON sr.session_key = rd.session_key
WHERE sr.dnf = 1
  AND s.session_type = 'Race' AND s.session_name = 'Race'
  AND sr.number_of_laps >= rd.race_distance; -- 0 
  
-- Verdict on DNF consistency:
-- 0 suspicious rows. Every DNF-flagged driver in Race sessions actually 
-- completed fewer laps than the race's max lap. DNF flags are trustworthy.
  
  
-- Points rollover check: for each driver across consecutive point-scoring sessions in the same year,
-- points_current at session N should equal points_start at session N+1
WITH ordered AS (
    SELECT cd.session_key, cd.driver_number, cd.points_start, cd.points_current,
           s.year, s.date_start,
           LAG(cd.points_current) OVER (
               PARTITION BY cd.driver_number, s.year 
               ORDER BY s.date_start
           ) AS prev_points_current
    FROM silver_championship_drivers cd
    JOIN silver_sessions s ON cd.session_key = s.session_key
)
SELECT COUNT(*) AS rollover_mismatches
FROM ordered
WHERE prev_points_current IS NOT NULL
  AND ABS(points_start - prev_points_current) > 0.01;--73
  
  
  
  
-- Verdict on championship rollover consistency:
-- 
-- 73 mismatches where session N's points_current != session N+1's points_start
-- for the same driver in the same year. All diffs are small (±1 to ±10 points).
--
-- These are NOT data errors. They reflect real F1 championship dynamics:
--   - Post-race DSQ decisions removing or reallocating points.
--   - Race Director penalties applied after a session ended.
--   - Investigation outcomes that modify results retroactively.
--
-- The largest mismatches (Leclerc -10 in 2025, Verstappen +10 in 2024, Hamilton
-- -8) match known F1 events. The championship_drivers table is a snapshot 
-- table; snapshots capture the standings AS OF that session, and inter-session 
-- adjustments break rollover arithmetic without being wrong.
--
-- For predictive modeling, this means: don't compute "points delta from race
-- to race" by subtracting consecutive point totals — that includes penalty 
-- adjustments. If you want "points earned in a single race," use 
-- session_result.points for that specific race.
  
  
  
-- For each driver-session, sum of (lap_end - lap_start + 1) across all stints should equal MAX(lap_number)
-- Simplified: check that stints don't overlap or leave big gaps
-- Real stint-tiling check: does MAX(lap_end) - MIN(lap_start) + 1 equal sum of stint lengths?
-- This validates that stints tile CONSECUTIVELY, regardless of where they start.
SELECT COUNT(*) AS session_driver_with_gaps
FROM (
    SELECT session_key, driver_number,
           SUM(lap_end - lap_start + 1) AS stint_lap_sum,
           MAX(lap_end) - MIN(lap_start) + 1 AS expected_span
    FROM silver_stints
    WHERE lap_start IS NOT NULL AND lap_end IS NOT NULL AND lap_end >= lap_start
    GROUP BY session_key, driver_number
) 
WHERE ABS(stint_lap_sum - expected_span) > 0;
-- Show 20 examples of driver-sessions with gaps and their stint layout




WITH driver_sessions_with_gaps AS (
    SELECT session_key, driver_number
    FROM (
        SELECT session_key, driver_number,
               SUM(lap_end - lap_start + 1) AS stint_lap_sum,
               MAX(lap_end) - MIN(lap_start) + 1 AS expected_span
        FROM silver_stints
        WHERE lap_start IS NOT NULL AND lap_end IS NOT NULL AND lap_end >= lap_start
        GROUP BY session_key, driver_number
    ) 
    WHERE ABS(stint_lap_sum - expected_span) > 0
    LIMIT 5
)
SELECT s.session_key, s.driver_number, s.stint_number, s.lap_start, s.lap_end, s.compound
FROM silver_stints s
JOIN driver_sessions_with_gaps g 
  ON s.session_key = g.session_key AND s.driver_number = g.driver_number
WHERE s.lap_start IS NOT NULL AND s.lap_end IS NOT NULL AND s.lap_end >= s.lap_start
ORDER BY s.session_key, s.driver_number, s.stint_number;



SELECT s.session_type, s.session_name, s.year, COUNT(*) AS n
FROM (
    SELECT session_key, driver_number
    FROM (
        SELECT session_key, driver_number,
               SUM(lap_end - lap_start + 1) AS stint_lap_sum,
               MAX(lap_end) - MIN(lap_start) + 1 AS expected_span
        FROM silver_stints
        WHERE lap_start IS NOT NULL AND lap_end IS NOT NULL AND lap_end >= lap_start
        GROUP BY session_key, driver_number
    ) 
    WHERE ABS(stint_lap_sum - expected_span) > 0
) g
JOIN silver_sessions s ON g.session_key = s.session_key
GROUP BY s.session_type, s.session_name, s.year
ORDER BY 4 DESC
LIMIT 10;



-- Verdict on stint tiling consistency:
--
-- 3,744 driver-sessions have stint gaps (stint_lap_sum != expected_span).
--
-- Almost all concentrated in Practice and Qualifying sessions across 2023-2024:
-- these are session types where drivers make multiple discrete runs with 
-- garage time between them. silver_stints only records "on-track continuous 
-- running" periods; garage time creates gaps between stints that aren't 
-- data errors — they reflect real F1 session behavior.
--
-- Race sessions tile cleanly. Race and Sprint stint data is trustworthy for 
-- analytical use.
--
-- For joining silver_laps to silver_stints in Practice/Qualifying contexts, 
-- use `lap_number BETWEEN stint.lap_start AND stint.lap_end` and expect some 
-- laps to have no matching stint.





-- ============================================================
-- Temporal Coverage
-- ============================================================


-- 1) Meetings per year
SELECT year, COUNT(*) AS meetings, 
       SUM(is_cancelled) AS cancelled,
       MIN(date_start) AS first_meeting,
       MAX(date_end) AS last_meeting
FROM silver_meetings
GROUP BY year
ORDER BY year;


-- 2) Meetings per month within each year (spot gaps)
SELECT year, 
       strftime('%m', date_start) AS month,
       COUNT(*) AS meetings
FROM silver_meetings
GROUP BY year, month
ORDER BY year, month;


-- 3) Sessions per meeting — should be ~5 typically
SELECT sessions_per_meeting, COUNT(*) AS n_meetings
FROM (
    SELECT meeting_key, COUNT(*) AS sessions_per_meeting
    FROM silver_sessions
    GROUP BY meeting_key
)
GROUP BY sessions_per_meeting
ORDER BY sessions_per_meeting;



-- 4) Which meetings have unusual session counts?
SELECT m.meeting_name, m.year, m.is_cancelled, COUNT(s.session_key) AS n_sessions
FROM silver_meetings m
LEFT JOIN silver_sessions s ON m.meeting_key = s.meeting_key
GROUP BY m.meeting_key, m.meeting_name, m.year, m.is_cancelled
HAVING n_sessions != 5
ORDER BY n_sessions, m.year;


-- 5) Telemetry coverage — which sessions have car_data?
SELECT s.year, s.session_type, 
       COUNT(DISTINCT s.session_key) AS total_sessions,
       COUNT(DISTINCT cd.session_key) AS sessions_with_telemetry
FROM silver_sessions s
LEFT JOIN silver_car_data cd ON s.session_key = cd.session_key
GROUP BY s.year, s.session_type
ORDER BY s.year, s.session_type;