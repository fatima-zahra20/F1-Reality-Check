-- Driver vs teammate (team-level lens)


-- Who out-qualified whom, and by how much?
-- Who scored more points, and by how much?
-- Whose race had more incidents/pit stops/lost time?
-- Did the two cars' strategies converge or split, and which paid off?


SELECT s.year, m.meeting_name, s.session_name,
       d1.team_name,
       d1.full_name AS driver, g1."position" AS grid_position, g1.lap_duration AS quali_lap_time,
       d2.full_name AS teammate, g2."position" AS teammate_grid_position, g2.lap_duration AS teammate_quali_lap_time,
       (g2."position" - g1."position") AS positions_ahead_of_teammate,
       (g1.lap_duration - g2.lap_duration) AS lap_time_delta_vs_teammate
FROM silver_starting_grid g1
JOIN silver_drivers d1 ON g1.session_key = d1.session_key AND g1.driver_number = d1.driver_number
JOIN silver_starting_grid g2 
    ON g1.session_key = g2.session_key AND g1.driver_number != g2.driver_number
JOIN silver_drivers d2 
    ON g2.session_key = d2.session_key AND g2.driver_number = d2.driver_number 
    AND d1.team_name = d2.team_name
JOIN silver_sessions s ON g1.session_key = s.session_key
JOIN silver_meetings m ON s.meeting_key = m.meeting_key
WHERE s.session_name = 'Qualifying'
  AND s.year = 2023
ORDER BY m.date_start, d1.team_name;


-- Who scored more points, and by how much?

SELECT s.year, m.meeting_name, s.session_name,
       d1.team_name,
       d1.full_name AS driver, sr1.points,
       d2.full_name AS teammate, sr2.points AS teammate_points,
       (sr1.points - sr2.points) AS points_delta_vs_teammate
FROM silver_session_result sr1
JOIN silver_drivers d1 ON sr1.session_key = d1.session_key AND sr1.driver_number = d1.driver_number
JOIN silver_session_result sr2 
    ON sr1.session_key = sr2.session_key AND sr1.driver_number != sr2.driver_number
JOIN silver_drivers d2 
    ON sr2.session_key = d2.session_key AND sr2.driver_number = d2.driver_number 
    AND d1.team_name = d2.team_name
JOIN silver_sessions s ON sr1.session_key = s.session_key
JOIN silver_meetings m ON s.meeting_key = m.meeting_key
WHERE s.session_name = 'Race'
  AND s.year = 2023
ORDER BY m.date_start, d1.team_name;





-- Whose race had more incidents/pit stops/lost time?
WITH driver_incidents AS (
    SELECT rc.session_key, rc.driver_number, COUNT(*) AS incident_count
    FROM silver_race_control rc
    WHERE rc.driver_number IS NOT NULL
    GROUP BY rc.session_key, rc.driver_number
),
driver_pit_summary AS (
    SELECT p.session_key, p.driver_number,
           COUNT(*) AS pit_stop_count,
           SUM(p.lane_duration) AS total_lane_time_lost
    FROM silver_pit p
    GROUP BY p.session_key, p.driver_number
)
SELECT s.year, m.meeting_name, s.session_name,d1.team_name,d1.full_name AS driver,
       COALESCE(inc1.incident_count, 0) AS incident_count,
       COALESCE(pit1.pit_stop_count, 0) AS pit_stop_count,
       pit1.total_lane_time_lost,sr1.duration_race_seconds AS total_race_time,
       d2.full_name AS teammate,
       COALESCE(inc2.incident_count, 0) AS teammate_incident_count,
       COALESCE(pit2.pit_stop_count, 0) AS teammate_pit_stop_count,
       pit2.total_lane_time_lost AS teammate_total_lane_time_lost,sr2.duration_race_seconds AS teammate_total_race_time
FROM silver_drivers d1
JOIN silver_drivers d2 ON d1.session_key = d2.session_key 
    AND d1.team_name = d2.team_name 
    AND d1.driver_number != d2.driver_number
JOIN silver_sessions s ON d1.session_key = s.session_key
JOIN silver_meetings m ON s.meeting_key = m.meeting_key

LEFT JOIN silver_session_result sr1 ON d1.session_key = sr1.session_key AND d1.driver_number = sr1.driver_number
LEFT JOIN silver_session_result sr2 ON d2.session_key = sr2.session_key AND d2.driver_number = sr2.driver_number

LEFT JOIN driver_incidents inc1 ON d1.session_key = inc1.session_key AND d1.driver_number = inc1.driver_number
LEFT JOIN driver_incidents inc2 ON d2.session_key = inc2.session_key AND d2.driver_number = inc2.driver_number
LEFT JOIN driver_pit_summary pit1 ON d1.session_key = pit1.session_key AND d1.driver_number = pit1.driver_number
LEFT JOIN driver_pit_summary pit2 ON d2.session_key = pit2.session_key AND d2.driver_number = pit2.driver_number
WHERE s.session_name = 'Race'
  AND s.year = 2023
ORDER BY m.date_start, d1.team_name;

  
  
  
-- Did the two cars' strategies converge or split, and which paid off?
WITH driver_strategy AS (
    SELECT st.session_key, st.driver_number, d.team_name, d.full_name, 
           GROUP_CONCAT(st.compound, ' -> ') AS compound_sequence
    FROM (
        SELECT session_key, driver_number, stint_number, compound
        FROM silver_stints
        WHERE lap_start IS NOT NULL AND lap_end >= lap_start
        ORDER BY session_key, driver_number, stint_number
    ) st
    JOIN silver_drivers d ON st.session_key = d.session_key AND st.driver_number = d.driver_number
    GROUP BY st.session_key, st.driver_number
)
SELECT s.year, m.meeting_name, s.session_name,
       t1.team_name,
       t1.full_name AS driver, t1.compound_sequence,
       sr1.position AS finish_position, sr1.points,
       t2.full_name AS teammate, t2.compound_sequence AS teammate_sequence,
       sr2.position AS teammate_finish_position, sr2.points AS teammate_points,
       CASE WHEN t1.compound_sequence = t2.compound_sequence THEN 'Same' ELSE 'Diverged' END AS strategy_match,
       CASE 
           WHEN sr1.position IS NULL OR sr2.position IS NULL THEN NULL
           WHEN sr1.position < sr2.position THEN t1.full_name
           WHEN sr2.position < sr1.position THEN t2.full_name
           ELSE 'Tied'
       END AS better_finishing_driver
FROM driver_strategy t1
JOIN driver_strategy t2 
    ON t1.session_key = t2.session_key 
    AND t1.team_name = t2.team_name 
    AND t1.driver_number != t2.driver_number
JOIN silver_sessions s ON t1.session_key = s.session_key
JOIN silver_meetings m ON s.meeting_key = m.meeting_key
LEFT JOIN silver_session_result sr1 ON t1.session_key = sr1.session_key AND t1.driver_number = sr1.driver_number
LEFT JOIN silver_session_result sr2 ON t2.session_key = sr2.session_key AND t2.driver_number = sr2.driver_number
WHERE s.session_name = 'Race'
  AND s.year = 2023
ORDER BY m.date_start, t1.team_name;
       
      
-- "Paid off" is approximated here as "finished ahead of the teammate," used only when 
-- strategy_match = 'Diverged'. This is a correlation, not a causal claim -- a driver 
-- could finish ahead for reasons unrelated to tyre strategy (pace, incidents, luck), 
-- and this query doesn't isolate strategy as the cause. 
--Genuinely attributing a result to strategy choice is diagnostic-phase work, not descriptive.    
       
      
 
	