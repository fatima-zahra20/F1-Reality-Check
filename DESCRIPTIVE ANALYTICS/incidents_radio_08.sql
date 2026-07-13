-- Incidents & external context

-- What race control events (flags, safety car, DRS status) occurred during the driver's race, and did any coincide with their pit stops or position changes?
-- Was the driver specifically named in any race control message (penalty, investigation, warning)?
-- What were the weather conditions during the race, and did they change mid-race (rain arriving, track drying)?


WITH lap_boundaries AS (
    SELECT l.session_key, l.driver_number, l.lap_number, l.date_start,
           LEAD(l.date_start) OVER (
               PARTITION BY l.session_key, l.driver_number 
               ORDER BY l.lap_number
           ) AS next_lap_start
    FROM silver_laps l
    WHERE l.date_start IS NOT NULL
)
SELECT s.year, m.meeting_name, s.session_name, d.full_name,lb.lap_number,
       (SELECT p.position FROM silver_position p WHERE p.session_key = lb.session_key
          AND p.driver_number = lb.driver_number
          AND p.date <= lb.next_lap_start
        ORDER BY p.date DESC LIMIT 1) AS position_at_lap_end,
		
       pits.stop_duration AS pit_box_stop_duration,
       pits.lane_duration AS pit_lane_entry_to_exit,
       (SELECT GROUP_CONCAT(DISTINCT rc.category || COALESCE(': ' || rc.flag, ''))
        FROM silver_race_control rc
        WHERE rc.session_key = lb.session_key AND rc.lap_number = lb.lap_number) AS race_control_glimpse
FROM lap_boundaries lb
LEFT JOIN silver_pit pits 
    ON lb.session_key = pits.session_key 
    AND lb.driver_number = pits.driver_number 
    AND lb.lap_number = pits.lap_number
JOIN silver_sessions s ON lb.session_key = s.session_key
JOIN silver_meetings m ON s.meeting_key = m.meeting_key
JOIN silver_drivers d ON lb.session_key = d.session_key AND lb.driver_number = d.driver_number
WHERE lb.next_lap_start IS NOT NULL
  AND s.session_name = 'Race'
  AND s.year = 2024
ORDER BY m.date_start, d.full_name, lb.lap_number;


-- Was the driver specifically named in any race control message (penalty, investigation, warning)?
SELECT s.year, m.meeting_name, s.session_name, d.full_name,
       rc.lap_number, rc.category, rc.flag, rc.message, rc.date
FROM silver_race_control rc
JOIN silver_sessions s ON rc.session_key = s.session_key
JOIN silver_meetings m ON s.meeting_key = m.meeting_key
JOIN silver_drivers d ON rc.session_key = d.session_key AND rc.driver_number = d.driver_number
WHERE rc.driver_number IS NOT NULL
  AND s.session_name = 'Race'
  AND s.year = 2023
ORDER BY m.date_start, d.full_name, rc.date;


-- What were the weather conditions during the race, and did they change mid-race (rain arriving, track drying)?

SELECT s.year, m.meeting_name, s.session_name,
       w.date, w.rainfall, w.pressure, w.track_temperature, w.wind_speed, w.wind_direction , w.humidity , w.air_temperature
FROM silver_weather w
JOIN silver_sessions s ON w.session_key = s.session_key
JOIN silver_meetings m ON s.meeting_key = m.meeting_key
--WHERE m.meeting_name = 'Austrian Grand Prix'
WHERE s.year = 2023
  AND s.session_name = 'Race'
ORDER BY w.date;


-- Team radio

-- How many radio messages were sent for this driver during the race, and at what points do they cluster (may signal key moments even without transcription)?
SELECT s.year, m.meeting_name, s.session_name, d.full_name,
       COUNT(*) OVER (PARTITION BY tr.session_key, tr.driver_number) AS total_radio_messages,
       (JULIANDAY(tr.date) - JULIANDAY(LAG(tr.date) OVER (PARTITION BY tr.session_key, tr.driver_number ORDER BY tr.date))) * 86400 AS seconds_since_prev_message,  tr.recording_url
FROM silver_team_radio tr
JOIN silver_sessions s ON tr.session_key = s.session_key
JOIN silver_meetings m ON s.meeting_key = m.meeting_key
JOIN silver_drivers d ON tr.session_key = d.session_key AND tr.driver_number = d.driver_number
WHERE s.session_name = 'Race'
  AND s.year = 2023
ORDER BY m.date_start, d.full_name, tr.date;