-- Tyre strategy

-- How many stints did the driver run, on which compounds, and for how many laps each?
-- What was the driver's tyre age at the start of each stint?
-- Team-level: did both cars run the same strategy (compound sequence) or diverge?

SELECT s.year, m.meeting_name, s.session_name, d.full_name,
       st.stint_number, st.compound, st.tyre_age_at_start,
       st.lap_start, st.lap_end
FROM silver_stints st
JOIN silver_sessions s ON st.session_key = s.session_key
JOIN silver_meetings m ON s.meeting_key = m.meeting_key
JOIN silver_drivers d 
    ON st.session_key = d.session_key AND st.driver_number = d.driver_number
WHERE s.session_name = 'Race'
  AND s.year = 2023
  AND st.lap_start IS NOT NULL
  AND st.lap_end >= st.lap_start
ORDER BY m.date_start, d.full_name, st.stint_number;

-- Team-level: did both cars run the same strategy (compound sequence) or diverge?

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
       t1.team_name, t1.full_name AS driver, t1.compound_sequence,
       t2.full_name AS teammate, t2.compound_sequence AS teammate_sequence,
       CASE WHEN t1.compound_sequence = t2.compound_sequence THEN 'Same' ELSE 'Diverged' END AS strategy_match
FROM driver_strategy t1
JOIN driver_strategy t2 
    ON t1.session_key = t2.session_key 
    AND t1.team_name = t2.team_name 
    AND t1.driver_number != t2.driver_number
JOIN silver_sessions s ON t1.session_key = s.session_key
JOIN silver_meetings m ON s.meeting_key = m.meeting_key
WHERE s.session_name = 'Race'
  AND s.year = 2023
ORDER BY m.date_start, t1.team_name;