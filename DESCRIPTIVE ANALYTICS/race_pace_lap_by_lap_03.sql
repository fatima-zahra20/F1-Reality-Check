-- Race pace, lap by lap

-- What was the driver's lap time trend across the race (improving, degrading, flat)?
-- How does the driver's pace compare to their teammate, lap by lap?
-- Were there specific laps with anomalous times (red flag, traffic, mistake), and where do they fall in the race?
-- What were the driver's sector strengths/weaknesses (which sector were they consistently fastest/slowest in)?
-- What was the driver's fastest lap of the race, and on which lap number/tyre compound did it occur?


-- What was the driver's lap time trend across the race (improving, degrading, flat)?

SELECT s.year, m.meeting_name, s.session_name,
       l.driver_number, d.full_name, l.lap_number, l.lap_duration,
       LAG(l.lap_duration) OVER (PARTITION BY l.session_key, l.driver_number ORDER BY l.lap_number) AS prev_lap_duration,
       l.lap_duration - LAG(l.lap_duration) OVER (PARTITION BY l.session_key, l.driver_number ORDER BY l.lap_number) AS lap_delta
FROM silver_laps l
JOIN silver_sessions s ON l.session_key = s.session_key
JOIN silver_meetings m ON s.meeting_key = m.meeting_key
JOIN silver_drivers d 
    ON l.session_key = d.session_key AND l.driver_number = d.driver_number
WHERE s.session_name = 'Sprint'
  AND l.lap_duration IS NOT NULL
ORDER BY m.date_start, d.full_name, l.lap_number;


-- How does the driver's pace compare to their teammate, lap by lap?
WITH team_laps AS (
    SELECT l.session_key, l.driver_number, d.team_name, d.full_name, 
           l.lap_number, l.lap_duration
    FROM silver_laps l
    JOIN silver_drivers d 
        ON l.session_key = d.session_key AND l.driver_number = d.driver_number
    WHERE l.lap_duration IS NOT NULL
)
SELECT s.year, m.meeting_name, s.session_name,
       t1.team_name, t1.full_name AS driver, t1.lap_number, t1.lap_duration,
       t2.full_name AS teammate, t2.lap_duration AS teammate_lap_duration,
       t1.lap_duration - t2.lap_duration AS delta_vs_teammate
FROM team_laps t1
JOIN team_laps t2 
    ON t1.session_key = t2.session_key 
    AND t1.team_name = t2.team_name 
    AND t1.lap_number = t2.lap_number
    AND t1.driver_number != t2.driver_number
JOIN silver_sessions s ON t1.session_key = s.session_key
JOIN silver_meetings m ON s.meeting_key = m.meeting_key
WHERE s.year = 2023 AND s.session_name = 'Sprint'
ORDER BY m.date_start, t1.team_name, t1.lap_number, t1.full_name;


-- Were there specific laps with anomalous times (red flag, traffic, mistake), and where do they fall in the race?
WITH clean_laps AS (
    SELECT l.session_key, l.driver_number, l.lap_number, l.lap_duration
    FROM silver_laps l
    WHERE l.lap_duration IS NOT NULL
      AND (l.is_pit_out_lap IS NULL OR l.is_pit_out_lap = 0)
),
driver_stats AS (
    SELECT session_key, driver_number,
           AVG(lap_duration) AS avg_lap,
           AVG(lap_duration * lap_duration) - AVG(lap_duration) * AVG(lap_duration) AS variance
    FROM clean_laps
    GROUP BY session_key, driver_number
)
-- Note: this flags laps that are statistically far from a driver's own average — 
-- it does NOT determine the cause (Safety Car, red flag, traffic, mistake). 
-- Cross-reference silver_race_control manually per finding if the cause matters.
-- Known: Safety Car periods span multiple laps but aren't logged on every lap in 
-- between deployment and clear, so a caution flag here would be unreliable anyway.
SELECT s.year, m.meeting_name, s.session_name, d.full_name, cl.lap_number, cl.lap_duration,
       ds.avg_lap, (cl.lap_duration - ds.avg_lap) AS diff_from_avg
FROM clean_laps cl
JOIN driver_stats ds ON cl.session_key = ds.session_key AND cl.driver_number = ds.driver_number
JOIN silver_sessions s ON cl.session_key = s.session_key
JOIN silver_meetings m ON s.meeting_key = m.meeting_key
JOIN silver_drivers d ON cl.session_key = d.session_key AND cl.driver_number = d.driver_number
WHERE s.session_name = 'Sprint'
  AND s.year = 2023
  AND (cl.lap_duration - ds.avg_lap) * (cl.lap_duration - ds.avg_lap) > 4 * ds.variance
ORDER BY m.date_start, d.full_name, cl.lap_number;



WITH clean_laps AS (
    SELECT l.session_key, l.driver_number, l.lap_number, l.lap_duration
    FROM silver_laps l
    WHERE l.lap_duration IS NOT NULL
      AND (l.is_pit_out_lap IS NULL OR l.is_pit_out_lap = 0)
),
driver_stats AS (
    SELECT session_key, driver_number,
           AVG(lap_duration) AS avg_lap,
           AVG(lap_duration * lap_duration) - AVG(lap_duration) * AVG(lap_duration) AS variance
    FROM clean_laps
    GROUP BY session_key, driver_number
)
-- Note: race_control_glimpse shows messages logged on this EXACT lap number only.
-- A caution period (e.g. Safety Car) can span multiple laps without a new message  
-- firing on every one of them — so a blank glimpse does NOT mean "no caution active."
-- Treat this as a starting point for manual investigation, not a definitive cause.
SELECT s.year, m.meeting_name, s.session_name, d.full_name, cl.lap_number, cl.lap_duration,
       ds.avg_lap, (cl.lap_duration - ds.avg_lap) AS diff_from_avg,
       (SELECT GROUP_CONCAT(DISTINCT rc.category || COALESCE(': ' || rc.flag, ''))
        FROM silver_race_control rc
        WHERE rc.session_key = cl.session_key AND rc.lap_number = cl.lap_number) AS race_control_glimpse
FROM clean_laps cl
JOIN driver_stats ds ON cl.session_key = ds.session_key AND cl.driver_number = ds.driver_number
JOIN silver_sessions s ON cl.session_key = s.session_key
JOIN silver_meetings m ON s.meeting_key = m.meeting_key
JOIN silver_drivers d ON cl.session_key = d.session_key AND cl.driver_number = d.driver_number
WHERE s.session_name = 'Sprint'
  AND s.year = 2023
  AND (cl.lap_duration - ds.avg_lap) * (cl.lap_duration - ds.avg_lap) > 4 * ds.variance
ORDER BY m.date_start, d.full_name, cl.lap_number;


-- A blank glimpse has two confirmed causes: (1) an event was happening but the message  
-- wasn't tagged to this lap number (e.g. a multi-lap Safety Car period confirmed via 
-- Albon, 2023 Azerbaijan Sprint lap 4), or (2) nothing needed logging because the anomaly 
-- was a private mechanical issue with no track-wide flag warranted (confirmed via Stroll, 
-- 2023 US GP Sprint lap 16, brake failure/DNF). Treat this column as a starting point for 
-- manual investigation, not a definitive cause.




-- What were the driver's sector strengths/weaknesses (which sector were they consistently fastest/slowest in)?
-- i can't compare sectors to each other because aren't equal length
WITH driver_best_sectors AS (
    SELECT l.session_key, l.driver_number,
           MIN(l.duration_sector_1) AS best_s1,
           MIN(l.duration_sector_2) AS best_s2,
           MIN(l.duration_sector_3) AS best_s3
    FROM silver_laps l
    GROUP BY l.session_key, l.driver_number
),
ranked AS (
    SELECT session_key, driver_number, best_s1, best_s2, best_s3,
           RANK() OVER (PARTITION BY session_key ORDER BY best_s1 ASC NULLS LAST) AS s1_rank,
           RANK() OVER (PARTITION BY session_key ORDER BY best_s2 ASC NULLS LAST) AS s2_rank,
           RANK() OVER (PARTITION BY session_key ORDER BY best_s3 ASC NULLS LAST) AS s3_rank
    FROM driver_best_sectors
)
SELECT s.year, m.meeting_name, s.session_name, d.full_name,
       r.best_s1, r.s1_rank,
       r.best_s2, r.s2_rank,
       r.best_s3, r.s3_rank
FROM ranked r
JOIN silver_sessions s ON r.session_key = s.session_key
JOIN silver_meetings m ON s.meeting_key = m.meeting_key
JOIN silver_drivers d ON r.session_key = d.session_key AND r.driver_number = d.driver_number
WHERE s.session_name = 'Sprint'
  AND s.year = 2023
ORDER BY m.date_start, d.full_name;--Liam Lawson lost control of his AlphaTauri and spun out into the gravel trap at Turn 2 on the very first lap




-- What was the driver's fastest lap of the race, and on which lap number/tyre compound did it occur?
WITH driver_lap_ranks AS (
    SELECT l.session_key, l.driver_number, l.lap_number, l.lap_duration,
           RANK() OVER (PARTITION BY l.session_key, l.driver_number ORDER BY l.lap_duration ASC) AS lap_rank
    FROM silver_laps l
    WHERE l.lap_duration IS NOT NULL
      AND (l.is_pit_out_lap IS NULL OR l.is_pit_out_lap = 0)
),
fastest_lap AS (
    SELECT session_key, driver_number, lap_number, lap_duration
    FROM driver_lap_ranks
    WHERE lap_rank = 1
),
field_rank AS (
    SELECT session_key, driver_number, lap_number, lap_duration,
           RANK() OVER (PARTITION BY session_key ORDER BY lap_duration ASC) AS field_rank
    FROM fastest_lap
)
SELECT s.year, m.meeting_name, s.session_name, d.full_name,
       fr.lap_number, fr.lap_duration, fr.field_rank,
       st.compound
FROM field_rank fr
JOIN silver_sessions s ON fr.session_key = s.session_key
JOIN silver_meetings m ON s.meeting_key = m.meeting_key
JOIN silver_drivers d ON fr.session_key = d.session_key AND fr.driver_number = d.driver_number
LEFT JOIN silver_stints st 
    ON fr.session_key = st.session_key 
    AND fr.driver_number = st.driver_number
    AND fr.lap_number BETWEEN st.lap_start AND st.lap_end
WHERE s.session_name = 'Sprint'
  AND s.year = 2023
ORDER BY m.date_start, fr.field_rank;


