-- Pit stops

-- How many pit stops did the driver make, on which laps?
-- What was the stop duration and total lane duration for each stop?
-- Did any stop go unusually long (a "disaster stop")?
-- Team-level: how does average pit stop duration compare between the two drivers/cars?



-- How many pit stops did the driver make, on which laps?
-- What was the stop duration and total lane duration for each stop?
SELECT s.year, m.meeting_name, s.session_name, d.full_name, 
       p.lap_number, p.stop_duration AS pit_box_stop_duration, p.lane_duration AS pit_lane_entry_to_exit,
       COUNT(*) OVER (PARTITION BY p.session_key, p.driver_number) AS driver_total_stops,
       SUM(p.lane_duration) OVER (PARTITION BY p.session_key, p.driver_number) AS driver_total_lane_time
FROM silver_pit p
JOIN silver_sessions s ON p.session_key = s.session_key
JOIN silver_meetings m ON s.meeting_key = m.meeting_key
JOIN silver_drivers d 
    ON p.session_key = d.session_key AND p.driver_number = d.driver_number
WHERE s.session_name = 'Race'
  AND s.year = 2024
ORDER BY m.date_start, d.full_name, p.lap_number;
-- stop_duration the source timing feed doesn't always capture it


-- Did any stop go unusually long (a "disaster stop")?
SELECT s.year, m.meeting_name, s.session_name, d.full_name,
       p.lap_number, p.stop_duration
FROM silver_pit p
JOIN silver_sessions s ON p.session_key = s.session_key
JOIN silver_meetings m ON s.meeting_key = m.meeting_key
JOIN silver_drivers d 
    ON p.session_key = d.session_key AND p.driver_number = d.driver_number
WHERE p.stop_duration IS NOT NULL
  AND p.stop_duration > 4.9  -- Tukey outlier fence: Upper fence = Q3 + (1.5 * IQR) = 3.4 + (1.5 * 1) = 3.4 + 1.5 = 4.9, from EDA_8 distribution stats
ORDER BY p.stop_duration DESC;



-- Team-level: how does average pit stop duration compare between the two drivers/cars?


WITH driver_pit_avg AS (
    SELECT p.session_key, p.driver_number,
           AVG(p.stop_duration) AS avg_stop_duration,
           AVG(p.lane_duration) AS avg_lane_duration,
           COUNT(*) AS total_stops
    FROM silver_pit p
    GROUP BY p.session_key, p.driver_number
)
SELECT s.year, m.meeting_name, s.session_name,
       d.team_name, d.full_name AS driver, 
       dpa.avg_stop_duration, dpa.avg_lane_duration, dpa.total_stops,
       dt.full_name AS teammate, 
       dpa2.avg_stop_duration AS teammate_avg_stop_duration, 
       dpa2.avg_lane_duration AS teammate_avg_lane_duration,
       dpa.avg_stop_duration - dpa2.avg_stop_duration AS stop_duration_delta,
       dpa.avg_lane_duration - dpa2.avg_lane_duration AS lane_duration_delta
FROM driver_pit_avg dpa
JOIN silver_drivers d ON dpa.session_key = d.session_key AND dpa.driver_number = d.driver_number
JOIN silver_drivers dt 
    ON d.session_key = dt.session_key AND d.team_name = dt.team_name AND d.driver_number != dt.driver_number
JOIN driver_pit_avg dpa2 ON dt.session_key = dpa2.session_key AND dt.driver_number = dpa2.driver_number
JOIN silver_sessions s ON dpa.session_key = s.session_key
JOIN silver_meetings m ON s.meeting_key = m.meeting_key
WHERE s.session_name = 'Race' AND s.year = 2024
ORDER BY m.date_start, d.team_name;