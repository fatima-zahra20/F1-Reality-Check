# F1 Reality Check: Question Bank
 
## Data Maturity Model
 
This project's analysis follows the four stages of the analytics maturity model:
 
This document is the **descriptive** layer only , pure facts about what happened in a race, no explanations yet. Diagnostic questions ("why") get appended in a follow-up section once the descriptive queries are run and reviewed.

## Scope: The Story of a Race, Driver & Team Level
 
Goal: reconstruct the full chronological story of a race, once at the **driver** level and once at the **team** level (comparing teammates).
 
### Pre-race, grid & setup
- [ ] What grid position did the driver start from, and what lap time earned it?
- [ ] Which drivers have a grid position but no recorded qualifying lap time, and how many of those are genuine DNS cases vs. started-but-crashed/red-flagged/deleted-lap cases?
- [ ] Team-level: what was the combined grid position of both cars (front-row lockout vs split across the field)?
### The start, lap 1
- [ ] How did each driver's position change from their qualifying grid slot to the end of lap 1, and how many places did they gain or lose?
- [ ] Was the driver involved in any lap-1 overtakes (as overtaker or overtaken)?
- [ ] Did any race control flag/incident fire in the opening laps involving this driver?
### Race pace, lap by lap
- [ ] What was the driver's lap time trend across the race (improving, degrading, flat)?
- [ ] How does the driver's pace compare to their teammate, lap by lap?
- [ ] Were there specific laps with anomalous times (red flag, traffic, mistake), and where do they fall in the race?
- [ ] What were the driver's sector strengths/weaknesses (which sector were they consistently fastest/slowest in)?
- [ ] What was the driver's fastest lap of the race, and on which lap number/tyre compound did it occur?
### Tyre strategy
- [ ] How many stints did the driver run, on which compounds, and for how many laps each?
- [ ] What was the driver's tyre age at the start of each stint?
- [ ] Team-level: did both cars run the same strategy (compound sequence) or diverge?
### Pit stops
- [ ] How many pit stops did the driver make, on which laps?
- [ ] What was the stop duration and total lane duration for each stop?
- [ ] Did any stop go unusually long (a "disaster stop")?
- [ ] Team-level: how does average pit stop duration compare between the two drivers/cars?
### Position dynamics across the race
- [ ] How did the driver's position evolve over the full race distance (a position-vs-lap trace)?
- [ ] How many total overtakes did the driver make, and how many suffered?
- [ ] At what points in the race did the biggest position swings happen (start, pit cycles, restarts)?
### Gaps & race context
- [ ] How did the driver's gap to the leader evolve over the race?
- [ ] How did the driver's gap to the car ahead/behind (interval) evolve, fighting, isolated, or lapped?
- [ ] Was the driver lapped by the leader at any point, and when?
### Incidents & external context
- [ ] What race control events (flags, safety car, DRS status) occurred during the driver's race, and did any coincide with their pit stops or position changes?
- [ ] Was the driver specifically named in any race control message (penalty, investigation, warning)?
- [ ] What were the weather conditions during the race, and did they change mid-race (rain arriving, track drying)?
### Team radio
- [ ] How many radio messages were sent for this driver during the race, and at what points do they cluster (may signal key moments even without transcription)?
### Finish & outcome
- [ ] What was the final classified position, and how does it compare to grid position (net gain/loss)?
- [ ] Did the driver finish, DNF, DNS, or get DSQ'd, and if DNF, at what lap?
- [ ] How many points did the driver score?
- [ ] What was the gap to the winner at the finish?
- [ ] Team-level: combined points haul for the race, and how it moved the constructor standings (`points_start` -> `points_current`).
### Driver vs teammate (team-level lens)
- [ ] Who out-qualified whom, and by how much?
- [ ] Who scored more points, and by how much?
- [ ] Whose race had more incidents/pit stops/lost time?
- [ ] Did the two cars' strategies converge or split, and which paid off?
