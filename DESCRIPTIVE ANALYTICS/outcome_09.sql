-- Finish & outcome

-- What was the final classified position, and how does it compare to grid position (net gain/loss)?
-- Did the driver finish, DNF, DNS, or get DSQ'd, and if DNF, at what lap?
-- How many points did the driver score?
-- What was the gap to the winner at the finish?
-- Team-level: combined points haul for the race, and how it moved the constructor standings (`points_start` -> `points_current`).



-- What was the final classified position, and how does it compare to grid position (net gain/loss)?
-- Did the driver finish, DNF, DNS, or get DSQ'd, and if DNF, at what lap?
-- How many points did the driver score?
-- What was the gap to the winner at the finish?

SELECT s.year, m.meeting_name, s.session_name, sr.driver_number, d.full_name,
       g."position" AS grid_position, sr."position" AS final_position,
       CASE 
           WHEN g."position" > sr."position" THEN 'gain'
           WHEN g."position" < sr."position" THEN 'loss'
           WHEN g."position" = sr."position" THEN 'stable'
           ELSE NULL
       END AS result_label,
       sr.dns, sr.dnf, sr.dsq, sr.number_of_laps AS laps_completed,
       sr.points,
       sr.gap_to_leader_seconds AS gap_to_winner_seconds,
       sr.gap_to_leader_laps AS gap_to_winner_laps
FROM silver_sessions s
JOIN silver_meetings m ON s.meeting_key = m.meeting_key
JOIN silver_session_result sr ON s.session_key = sr.session_key
JOIN silver_drivers d 
    ON sr.session_key = d.session_key AND sr.driver_number = d.driver_number
JOIN silver_starting_grid g 
    ON g.meeting_key = m.meeting_key AND g.driver_number = sr.driver_number
JOIN silver_sessions gs 
    ON g.session_key = gs.session_key AND gs.session_name = 'Sprint Qualifying'
WHERE s.year = 2023 
  AND s.session_name = 'Sprint'
ORDER BY m.date_start, d.full_name;




-- Team-level: combined points haul for the race, and how it moved the constructor standings (`points_start` -> `points_current`).
WITH team_points AS (
    SELECT sr.session_key, d.team_name, SUM(sr.points) AS points_from_results
    FROM silver_session_result sr
    JOIN silver_drivers d ON sr.session_key = d.session_key AND sr.driver_number = d.driver_number
    GROUP BY sr.session_key, d.team_name
)
SELECT s.year, m.meeting_name, s.session_name,ct.team_name,ct.points_start, ct.points_current,
       (ct.points_current - ct.points_start) AS points_gained_per_standings,
       tp.points_from_results,ct.position_start, ct.position_current
FROM silver_championship_teams ct
JOIN silver_sessions s ON ct.session_key = s.session_key
JOIN silver_meetings m ON s.meeting_key = m.meeting_key
LEFT JOIN team_points tp ON ct.session_key = tp.session_key AND ct.team_name = tp.team_name
WHERE s.session_name = 'Sprint'
  AND s.year = 2023
ORDER BY m.date_start, ct.team_name;

--silver_session_result has zero rows for 7 specific, non-cancelled 2023 sessions: 
--Bahrain Race, Azerbaijan Sprint, Hungarian Qualifying, Belgian Qualifying, Mexico City Practice 3, Las Vegas Practice 1, Austrian Sprint Qualifying, 
--Qatar Sprint Qualifying. 
--Confirmed via is_cancelled = 0 that these sessions genuinely took place 
-- this is a real ingestion gap, not a data-model artifact. 
--(Separately, Emilia Romagna's full 5-session zero-count is NOT a gap 
-- that meeting has is_cancelled = 1, correctly reflecting the real-world flood cancellation.) 

--Loosely, 3 of the 7 gaps are Sprint-weekend sessions -- possibly worth revisiting if Sprint-specific coverage becomes relevant later, but not conclusive from this alone.

SELECT m.meeting_name, s.session_name, s.is_cancelled
FROM silver_sessions s
JOIN silver_meetings m ON s.meeting_key = m.meeting_key
WHERE (m.meeting_name, s.session_name) IN (
    ('Bahrain Grand Prix', 'Race'),
    ('Azerbaijan Grand Prix', 'Sprint'),
	('Emilia Romagna Grand Prix','Race'),
    ('Austrian Grand Prix', 'Sprint Qualifying'),
    ('Hungarian Grand Prix', 'Qualifying'),
    ('Belgian Grand Prix', 'Qualifying'),
    ('Qatar Grand Prix', 'Sprint Qualifying'),
    ('Mexico City Grand Prix', 'Practice 3'),
    ('Las Vegas Grand Prix', 'Practice 1')
)
AND m.year = 2023;