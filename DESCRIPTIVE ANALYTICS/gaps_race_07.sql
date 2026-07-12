-- Gaps & race context


-- How did the driver's gap to the leader evolve over the race?
-- How did the driver's gap to the car ahead/behind (interval) evolve, fighting, isolated, or lapped?
-- Was the driver lapped by the leader at any point, and when?


WITH lap_boundaries AS (
    SELECT l.session_key, l.driver_number, l.lap_number, l.date_start,
           LEAD(l.date_start) OVER (
               PARTITION BY l.session_key, l.driver_number 
               ORDER BY l.lap_number
           ) AS next_lap_start
    FROM silver_laps l
    WHERE l.date_start IS NOT NULL
)
SELECT s.year, m.meeting_name, s.session_name, d.full_name, lb.lap_number,
       (SELECT p.position
        FROM silver_position p
        WHERE p.session_key = lb.session_key
          AND p.driver_number = lb.driver_number
          AND p.date <= lb.next_lap_start
        ORDER BY p.date DESC
        LIMIT 1) AS position,
       (SELECT i.gap_to_leader_seconds
        FROM silver_intervals i
        WHERE i.session_key = lb.session_key
          AND i.driver_number = lb.driver_number
          AND i.date <= lb.next_lap_start
        ORDER BY i.date DESC
        LIMIT 1) AS gap_to_leader_seconds,
       (SELECT i.gap_to_leader_laps
        FROM silver_intervals i
        WHERE i.session_key = lb.session_key
          AND i.driver_number = lb.driver_number
          AND i.date <= lb.next_lap_start
        ORDER BY i.date DESC
        LIMIT 1) AS gap_to_leader_laps
FROM lap_boundaries lb
JOIN silver_sessions s ON lb.session_key = s.session_key
JOIN silver_meetings m ON s.meeting_key = m.meeting_key
JOIN silver_drivers d ON lb.session_key = d.session_key AND lb.driver_number = d.driver_number
WHERE lb.next_lap_start IS NOT NULL
  AND s.session_name = 'Race'
  AND s.year = 2023
ORDER BY m.date_start, d.full_name, lb.lap_number;




-- How did the driver's gap to the car ahead/behind (interval) evolve, fighting, isolated, or lapped?

WITH lap_boundaries AS (
    SELECT l.session_key, l.driver_number, l.lap_number, l.date_start,
           LEAD(l.date_start) OVER (
               PARTITION BY l.session_key, l.driver_number 
               ORDER BY l.lap_number
           ) AS next_lap_start
    FROM silver_laps l
    WHERE l.date_start IS NOT NULL
),
gap_trace AS (
    SELECT lb.session_key, lb.driver_number, lb.lap_number,
           (SELECT p.position
            FROM silver_position p
            WHERE p.session_key = lb.session_key
              AND p.driver_number = lb.driver_number
              AND p.date <= lb.next_lap_start
            ORDER BY p.date DESC
            LIMIT 1) AS position,
           (SELECT i.interval_seconds
            FROM silver_intervals i
            WHERE i.session_key = lb.session_key
              AND i.driver_number = lb.driver_number
              AND i.date <= lb.next_lap_start
            ORDER BY i.date DESC
            LIMIT 1) AS gap_to_car_ahead_seconds,
           (SELECT i.interval_laps
            FROM silver_intervals i
            WHERE i.session_key = lb.session_key
              AND i.driver_number = lb.driver_number
              AND i.date <= lb.next_lap_start
            ORDER BY i.date DESC
            LIMIT 1) AS lapped_by_car_ahead
    FROM lap_boundaries lb
    WHERE lb.next_lap_start IS NOT NULL
)
SELECT s.year, m.meeting_name, s.session_name, d.full_name, gt.lap_number,
       gt.position, gt.gap_to_car_ahead_seconds, gt.lapped_by_car_ahead,
       CASE 
           WHEN gt.lapped_by_car_ahead IS NOT NULL THEN 'lapped'
           WHEN gt.gap_to_car_ahead_seconds <= 1.0 THEN 'fighting'
           WHEN gt.gap_to_car_ahead_seconds IS NOT NULL THEN 'isolated'
           ELSE NULL
       END AS race_state
FROM gap_trace gt
JOIN silver_sessions s ON gt.session_key = s.session_key
JOIN silver_meetings m ON s.meeting_key = m.meeting_key
JOIN silver_drivers d ON gt.session_key = d.session_key AND gt.driver_number = d.driver_number
WHERE s.session_name = 'Race'
  AND s.year = 2023
ORDER BY m.date_start, d.full_name, gt.lap_number;
-- Race state labels, based on gap_to_car_ahead only (gap-to-behind not computed, see notes):
--   'lapped'   = the car ahead has gained a full lap (interval_laps populated) -- rare, 
--                65 rows total across the whole dataset (see EDA notes)
--   'fighting' = within 1.0s of the car ahead -- the official F1 DRS detection zone, 
--                borrowed from the sport's own rules rather than a statistical cutoff
--   'isolated' = more than 1.0s behind the car ahead -- open space in front, no one to 
--                race or draft off; a normal, common race state, not a bad sign. Only 
--                describes the gap AHEAD -- a driver labeled 'isolated' could still have 
--                someone close behind putting them under pressure; that's not captured here.




-- Was the driver lapped by the leader at any point, and when?
WITH lap_boundaries AS (
    SELECT l.session_key, l.driver_number, l.lap_number, l.date_start,
           LEAD(l.date_start) OVER (
               PARTITION BY l.session_key, l.driver_number 
               ORDER BY l.lap_number
           ) AS next_lap_start
    FROM silver_laps l
    WHERE l.date_start IS NOT NULL
),
leader_gap_trace AS (
    SELECT lb.session_key, lb.driver_number, lb.lap_number,
           (SELECT p.position
            FROM silver_position p
            WHERE p.session_key = lb.session_key
              AND p.driver_number = lb.driver_number
              AND p.date <= lb.next_lap_start
            ORDER BY p.date DESC
            LIMIT 1) AS position_held,
           (SELECT i.gap_to_leader_laps
            FROM silver_intervals i
            WHERE i.session_key = lb.session_key
              AND i.driver_number = lb.driver_number
              AND i.date <= lb.next_lap_start
            ORDER BY i.date DESC
            LIMIT 1) AS gap_to_leader_laps
    FROM lap_boundaries lb
    WHERE lb.next_lap_start IS NOT NULL
)
SELECT s.year, m.meeting_name, s.session_name, d.full_name,
       lgt.lap_number AS lapped_on_lap,
       lgt.position_held,
       lgt.gap_to_leader_laps
FROM leader_gap_trace lgt
JOIN silver_sessions s ON lgt.session_key = s.session_key
JOIN silver_meetings m ON s.meeting_key = m.meeting_key
JOIN silver_drivers d ON lgt.session_key = d.session_key AND lgt.driver_number = d.driver_number
WHERE lgt.gap_to_leader_laps IS NOT NULL
  AND s.session_name = 'Race'
  AND s.year = 2023
ORDER BY m.date_start, d.full_name, lgt.lap_number;