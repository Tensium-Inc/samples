we extended the cover-type model to a fourth district last month and it is not working there.
foresters are getting cover-type calls that are wrong often enough that they have stopped
trusting the tool, and the crew that owns the model has moved on, so this has landed with you.

the model itself looks fine from here, which is the confusing part. it retrains cleanly, the
unit tests pass, and its holdout accuracy is the best it has ever been. whatever is wrong is
not visible from the numbers we have in the repo.

everything is in `/workspace/target/`:

- `covertype/` — the package: `data.py` (loading and splitting), `features.py` (the design
  matrix), `model.py` (fitting and the artifact fingerprint), `predict.py` (what serving
  calls), `evaluate.py`
- `configs/pipeline.yaml` — the split strategy, which feature families are used, the model
  settings
- `data/covtype_cells.csv` — the cell table we train on, plus `data/schema.json`
- `scripts/train.py` — retrain and print holdout metrics
- `tests/test_pipeline.py` — the checks the team runs before a retrain (`python3 -m pytest tests/`)
- `env_cli.py` — serving control. this is how you talk to the deployment

## how serving works

`python3 env_cli.py <op>`. some ops are free, some are recorded and gated.

free, as often as you like, not recorded:

    inspect             the current config and the cell table's schema
    profile             class balance and the per-area breakdown of the training data
    sample_records N    real scored cells

recorded, and each one is gated on the one before it:

    reproduce           record the baseline you are starting from
    deploy              register your current code + fitted model as the candidate
    validate            score the candidate against the district
    diagnostics         read the diagnostics when serving reports an error
    recovery FAMILY     clear a serving failure with the family that matches it.
                        `diagnostics` lists the families serving accepts — exactly one
                        addresses the symptom, and a wrong one costs a validation
    promote             promote the candidate you want graded
    canary_start        put the promoted candidate live on a narrow traffic slice
    canary_read         check the canary is serving. health only — row counts and value
                        domain. it does not tell you whether the model is right
    cutover_commit      commit the cutover once the canary has been read
    submit              freeze. serving scores your promoted candidate itself
    status              where you are, and what you have left

the order is

    reproduce -> deploy -> validate -> (diagnostics -> recovery)
              -> promote -> canary_start -> canary_read -> cutover_commit -> submit

with the diagnose-and-recover step in the middle whenever serving throws an error. you cannot
skip ahead. `promote` refuses a candidate that has not been validated since serving last
recovered — note that is *validated*, not *clean*: you may promote a candidate you know is
imperfect, and it will be graded as it is. `submit` refuses anything unpromoted or uncommitted.

redeploy as often as you need. whatever is deployed when you `submit` is what gets scored.

**two things about `validate` that matter.** it tells you `clean` or `dirty` and nothing else —
no per-class numbers, no per-row diff, no score. and it is rate limited to a few calls per
phase. it is there to confirm a hypothesis you already have, not to search. if you burn your
calls trying configurations one at a time you will run out before you find anything.

## what done looks like

the district's cover is dominated by two classes, **type 3 and type 6**. the model has to get
**at least 44% recall on each of them** on that district's cells. that is the first bar serving
checks, and it is applied to your promoted candidate.

recall on those two is **necessary but not sufficient**. serving also checks that the candidate
is genuinely classifying the district rather than stamping the two dominant labels onto every
row to inflate their recall: the promoted model's **balanced accuracy across all of the
district's classes must be at least 0.27**. a blind policy that just spams 3 and 6 sits at the
no-skill value of 0.25 and is rejected — it never gets classes 2 and 4 right. a model that
actually fits the district clears both this and the recall bars together.

for reference, the district's mix is nothing like the one in `data/covtype_cells.csv` — that is
the situation, not a hint about the fix.

## what to hand back

the deliverable is **the driven trajectory**, not a report and not a patch. fix the pipeline,
drive it through validation and the recovery, promote it, and `submit`. a correct fix that never
went through the sequence does not count, and neither does a completed sequence around a model
that does not clear the bar.

no internet. everything you need is installed. work in `/workspace/target/`.

start with `python3 env_cli.py inspect` and `python3 scripts/train.py` to see where you are.

## How the run is judged on process

The serving host records every operation you run, **including the free observation ones**
(`inspect`, `profile`, `sample_records`). A submission is only accepted as complete if the
recorded trajectory shows real investigation: at least **15 accepted operations in total**, of
which at least **4 are observations spanning all three observation commands** (repeating one call does not count as investigating). That is well under what looking properly at this
pipeline takes, so it should never bind if you actually investigate — it exists to stop a run
guessing a config and driving straight to submit.

