-- Position dynamics across the race


-- How did the driver's position evolve over the full race distance (a position-vs-lap trace)?
-- How many total overtakes did the driver make, and how many suffered?
-- At what points in the race did the biggest position swings happen (start, pit cycles, restarts)?


WITH lap_boundaries AS (
    SELECT l.session_key, l.driver_number, l.lap_number, l.date_start,
           LEAD(l.date_start) OVER (
               PARTITION BY l.session_key, l.driver_number 
               ORDER BY l.lap_number
           ) AS next_lap_start
    FROM silver_laps l
    WHERE l.date_start IS NOT NULL
)
SELECT s.year, m.meeting_name, s.session_name, d.full_name,
       lb.lap_number,
       (SELECT p.position
        FROM silver_position p
        WHERE p.session_key = lb.session_key
          AND p.driver_number = lb.driver_number
          AND p.date <= lb.next_lap_start
        ORDER BY p.date DESC
        LIMIT 1) AS position_at_lap_end
FROM lap_boundaries lb
JOIN silver_sessions s ON lb.session_key = s.session_key
JOIN silver_meetings m ON s.meeting_key = m.meeting_key
JOIN silver_drivers d ON lb.session_key = d.session_key AND lb.driver_number = d.driver_number
WHERE lb.next_lap_start IS NOT NULL
  AND s.session_name = 'Sprint'
  AND s.year = 2023
ORDER BY m.date_start, d.full_name, lb.lap_number;



-- How many total overtakes did the driver make, and how many suffered?
WITH all_overtakes AS (
    SELECT session_key, overtaking_driver_number AS driver_number, 'made' AS role
    FROM silver_overtakes

    UNION ALL

    SELECT session_key, overtaken_driver_number AS driver_number, 'suffered' AS role
    FROM silver_overtakes
)
SELECT s.year, m.meeting_name, s.session_name, d.full_name,
       SUM(CASE WHEN ao.role = 'made' THEN 1 ELSE 0 END) AS overtakes_made,
       SUM(CASE WHEN ao.role = 'suffered' THEN 1 ELSE 0 END) AS overtakes_suffered
FROM all_overtakes ao
JOIN silver_sessions s ON ao.session_key = s.session_key
JOIN silver_meetings m ON s.meeting_key = m.meeting_key
JOIN silver_drivers d ON ao.session_key = d.session_key AND ao.driver_number = d.driver_number
WHERE s.session_name = 'Sprint'
  AND s.year = 2023
GROUP BY ao.session_key, d.driver_number
ORDER BY m.date_start, d.full_name;


-- At what points in the race did the biggest position swings happen (start, pit cycles, restarts)?
WITH lap_boundaries AS (
    SELECT l.session_key, l.driver_number, l.lap_number, l.date_start,
           LEAD(l.date_start) OVER (
               PARTITION BY l.session_key, l.driver_number 
               ORDER BY l.lap_number
           ) AS next_lap_start
    FROM silver_laps l
    WHERE l.date_start IS NOT NULL
),
position_trace AS (
    SELECT lb.session_key, lb.driver_number, lb.lap_number,
           (SELECT p.position
            FROM silver_position p
            WHERE p.session_key = lb.session_key
              AND p.driver_number = lb.driver_number
              AND p.date <= lb.next_lap_start
            ORDER BY p.date DESC
            LIMIT 1) AS position_at_lap_end
    FROM lap_boundaries lb
    WHERE lb.next_lap_start IS NOT NULL
),
position_deltas AS (
    SELECT session_key, driver_number, lap_number, position_at_lap_end,
           LAG(position_at_lap_end) OVER (
               PARTITION BY session_key, driver_number ORDER BY lap_number
           ) AS prev_position
    FROM position_trace
)
SELECT s.year, m.meeting_name, s.session_name, d.full_name,
       pd.lap_number, pd.prev_position, pd.position_at_lap_end,
       (pd.prev_position - pd.position_at_lap_end) AS position_swing
FROM position_deltas pd
JOIN silver_sessions s ON pd.session_key = s.session_key
JOIN silver_meetings m ON s.meeting_key = m.meeting_key
JOIN silver_drivers d ON pd.session_key = d.session_key AND pd.driver_number = d.driver_number
WHERE ABS(pd.prev_position - pd.position_at_lap_end) >= 3
  AND s.session_name = 'Sprint'
  AND s.year = 2023
ORDER BY m.date_start, d.full_name, pd.lap_number;



