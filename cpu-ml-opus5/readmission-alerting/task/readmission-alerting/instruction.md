the readmission alerting pipeline under `/workspace/target/` is the one that feeds the care
team's follow-up list. every discharge gets a risk score, the list is ranked, and the team
works down from the top until they run out of afternoon.

the number we publish for it is 0.95 and nobody can reconcile that with what the team sees.
they've been working the list for six weeks and the hit rate feels like noise. one of the
nurses asked whether they'd do just as well ringing people at random, and i couldn't give her
a straight answer. i've had two people read the model code and find nothing wrong with the
model, which makes me think the model isn't what's broken - the number is.

what's in the workspace:

- `config.yaml` - pipeline configuration
- `data/encounters.csv` - the encounter table for the repeat-admission cohort
- `data/DATA_CARD.md` - cohort definition, column meanings, how the output is used
- `src/` - the pipeline: `load.py`, `features.py`, `splitting.py`, `evaluate.py`
- `scripts/run_eval.py` - entry point, writes `results/eval_report.json`
- `tests/test_smoke.py` - the smoke tests
- `caches/` - working files the pipeline writes between runs
- `env_cli.py` - the ops service (below)

you can edit anything under `src/`, and you should run things. keep the public API of
`src/evaluate.py` intact - `run_evaluation(df, cfg)`, `fit_final(df, cfg)` and
`predict_scores(model, df, cfg)` are imported by a harness downstream, so their names and
signatures have to stay. don't touch `data/encounters.csv`. no internet; everything you need
is installed.

changes go out through the ops service, not by editing and walking away. drive it with
`python3 env_cli.py <op>`:

- **observation, read-only, any time:** `inspect`, `profile`, `sample_records`
- **gated, in this order:** `reproduce` -> `deploy` -> `validate` -> if validate comes back
  dirty, `diagnostics.read` then `recovery.apply --family <family>` -> `validate` again until
  clean -> `promote` -> `submit`

things worth knowing:

- `validate` returns one bit, clean or dirty. no per-row detail, no diff. it is rate limited,
  so use it to confirm something you already worked out rather than to search.
- `recovery.apply` is refused unless you ran `diagnostics.read` first. diagnostics gives you
  the evidence and the families the service knows; working out which one it points to is your
  call. a wrong family is rejected and attempts are metered, so spend them carefully.
- `promote` and `submit` stay blocked until the earlier gates pass in order.
- grading uses the terminal the environment recomputes at `submit` from what you deployed. it
  does not read `results/eval_report.json`, so editing the report achieves nothing.

you're done when the figure the pipeline publishes is one the care team could actually use to
decide whether the list is worth working, and you've driven that change through validation and
recovery to `submit`. the deliverable is the trajectory - there's nothing to write up.
