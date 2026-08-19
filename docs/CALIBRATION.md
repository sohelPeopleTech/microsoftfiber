# Calibrating the meaningful-delay cut-off

The design doc requires "a defined cut-off for what counts as a meaningful
delay (e.g., same-day approval does not count as a failure)". This is where
that number comes from, so nobody has to take 48 hours on faith.

## The rule

A ticket with both a `DeniedDate` and an `ApprovedDate` is classified by the
gap between them:

```
delay_hours <= cut-off   ->  same_day_approved          (normal turnaround)
delay_hours >  cut-off   ->  denied_then_approved_late  (service failure)
```

## What the labelled sample allows

`Expected_Classifications` in the synthetic workbook labels all 60 tickets.
Among the ones with both dates:

| Bucket | n | Delay range |
|--------|---|-------------|
| `same_day_approved` | 15 | up to **29.0h** |
| `denied_then_approved_late` | 18 | from **145.0h** (6.0 days) |

There is a clean gap between the two: no labelled ticket sits between 29h and
145h. Any cut-off in **[29, 145) hours** reproduces the ground truth exactly.
That band is asserted in `tests/test_classifier.py`, so if a future extract
narrows it, the test fails rather than the note going quietly stale.

## Why 48

- It sits inside the safe band with room either side, so a couple of borderline
  tickets in a future extract will not flip the classification.
- "Two business days" is a boundary a reviewer can argue with on policy grounds
  rather than on arithmetic grounds — which is the argument worth having.
- It is generous to the capacity team. Nothing under two days is called a
  failure, so the flagged set is hard to dismiss as over-counting.

Change it in `config.json`, not in code. Every output already prints the
cut-off it ran with.

## What this calibration does *not* establish

The labelled sample is synthetic, and its gap between 29h and 145h is wider
than real ICM data will be. Once real tickets are available:

1. Re-run the same two-bucket table above on the real delay distribution.
2. If the gap has closed, the cut-off becomes a genuine policy decision rather
   than a data-derived one — take it to whoever owns the capacity SLA and
   record their answer here.
3. Re-run `pytest tests/test_classifier.py` with the real labels attached.

## Related knobs

| Setting | Default | What moving it changes |
|---------|---------|------------------------|
| `meaningful_delay_hours` | 48 | what counts as a failure at all |
| `severity_medium_days` | 7 | when a delay stops being routine |
| `severity_high_days` | 21 | when a delay becomes a headline |
| `unfulfilled_cap_days` | 365 | how much one old open ticket can dominate the ranking |
| `annualisation_days` | 365 | the denominator turning ARR into daily exposure |
