## Prescriptive Analysis: what should have been done differently?

The fourth and last of the four analytics headlines. Descriptive asks what
happened, diagnostic asks why, predictive asks what happens next, and
prescriptive asks **what action would have produced a better outcome**.

Built on the same silver and gold tables as the other phases, but on top of the
diagnostic layer rather than beside it: every recommendation here is an
arithmetic consequence of a coefficient that `s05_diagnostic` or
`s05b_prescriptive`
already fitted and published. Nothing new is estimated in this phase. That is
deliberate, because a prescription is only as trustworthy as the model it came
from, and reusing the published model means the number on the page can be traced
to a fitted coefficient with a confidence interval attached.

Shipped as the **Prescribe** page (`dashboard/views/prescribe.py`), section 3,
"What could have been better".

**Convention:** `[X]` means the shipped page already answers it. `[ ]` means open.
Unlike the descriptive and diagnostic banks, this one was written after the
feature existed, so it doubles as an audit of what was built.

### Before you prescribe anything

Prescriptive is the most dangerous of the four headlines, because it tells
someone what they should have done. These are the rules that keep it honest.

- [X] Never rebuild the outcome from coefficients. Take the real lap and add the
      estimated change to it, so the lap's own residual travels with it
      untouched. Rebuilding from coefficients quietly replaces the driver with
      the average of everyone.
- [X] Separate what someone chose from what merely happened to them. Weather is
      not a decision. Two blocks, two kinds of claim.
- [X] Refuse values no sensor has recorded. Combinations nobody has run are the
      whole point; a track temperature that has never existed is extrapolation.
- [ ] State the size of the recommendation against the size of the unexplained
      part, every time, in the same units. A 0.2s recommendation sitting beside a
      2.0s residual is not advice, it is noise with a decimal point.
- [ ] Never state the unexplained share as a literal. **Currently broken:** the
      page computes 69.61% for its headline panel and hardcodes 76% in the
      closing warning and in `lap_counterfactual.py`'s docstring. 76% was true
      before the 2026-07-27 backfill and the caution-flag rewrite. Compute it.

### What can be changed, and what only happened

- [X] Which recorded factors could a team or driver actually have chosen
      differently, and which were the same for every car on track?
      (six choice levers: compound, tyre age, gap to the car ahead, dirty air,
      out of position, being lapped; eight condition levers: rain, track and air
      temperature, humidity, wind speed and direction, sector yellow, lap number
      as a fuel proxy)
- [X] Why can choices be identified but conditions not, from the same data?
      (choices are estimated against other cars on the same lap of the same race,
      where fuel, track state and weather are shared and difference away;
      conditions are identical across those cars and vanish under that
      differencing, so they can only come from the pooled model)
- [ ] Should conditions be offered as levers at all, given nobody chose them?
      (they answer "what would a different afternoon have given", which is a
      legitimate question and a different one from "what should the team have
      done". Decide whether the page separates them clearly enough)

### What one change is worth

- [X] On a specific lap, what is each individual change worth in seconds?
      (`new lap = real lap + sum of the changes`, one bar per changed lever)
- [X] Does the tyre recommendation account for how old the tyre is?
      (compound carries a level term and an interaction with tyre age, so
      quoting either alone describes a tyre that does not exist)
- [ ] Does the same lever have different value at different circuits?
      (Monaco against Monza as the extreme pair; currently the coefficient is
      pooled across all 81 races)
- [ ] Are the confidence intervals on each coefficient shown where the
      recommendation is made, or only in the diagnostic section?

### The best realistic case

- [X] What is the best combination available within this race's observed range?
      (a button that sets every choice lever to its best bounded value)
- [ ] Is that combination stable? Would a neighbouring lap of the same race
      recommend something different, and by how much?
      (run the best-case search across every lap of one race, compare)
- [ ] Has any recommendation ever been checked against a lap where a driver
      genuinely did the recommended thing? (the only real validation available:
      find laps matching the prescribed combination and compare actual times)

### Staying inside the evidence

- [X] Where does each slider's range come from?
      (per-race bounds, with a toggle widening to everything recorded across four
      seasons; gap to the car ahead is capped at 10s because beyond that it is
      clear track either way)
- [ ] What happens when a lever has no recorded range for a race, or a
      degenerate one where low equals high? (the code substitutes low to low+1;
      confirm that never reaches the page as a real recommendation)
- [ ] Which laps cannot be prescribed for at all, and does the page say why?
      (a lap with no recorded duration, missing factors, or a blocked reference)

### Honesty and magnitude

- [ ] Given the counterfactual model's within-lap R2 of 0.1177, what magnitude
      of recommendation is meaningful rather than noise? Establish a floor and
      say so on the page.
- [ ] For a typical lap, what is the ratio of the largest available
      recommendation to that lap's own unexplained residual?
      (the page already prints this ratio for the current lap; summarise it
      across all laps so the reader knows whether their lap is typical)
- [ ] Does the page ever imply a recommendation would definitely have worked?
      (read every sentence in section 3 for the difference between "was worth"
      and "would have gained")

### Bridge to the predictive phase

- [ ] Which of the six choice levers are knowable before a race starts?
      (only these can become predictive features; the rest are outcomes. This
      question is the direct link into the predictive layer's "known as of when"
      contract)
