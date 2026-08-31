# Tensium — Long-Horizon Coding RL Environment Samples

Complete long-horizon coding RL tasks for ML-engineering training, built and
evaluated by Tensium. Everything is browsable in place — no downloads needed.

## Sample packs

### [cpu-ml-opus5](cpu-ml-opus5/) — CPU ML Engineering, evaluated on Claude Opus

Five complete tasks with full environments, graders, gold and alternative
solutions, negative controls, and Claude Opus agent traces. Start with the
[pack overview](cpu-ml-opus5/overview.pdf).
Broad range of difficulties for current models and some much more difficult to give room for models better than Opus 5. 

| Sample | Domain | Opus 5 pass@4 |
|---|---|---|
| [nyc-trip-duration-serving](cpu-ml-opus5/nyc-trip-duration-serving/) | Trip-duration model serving and regression repair | 2/4 |
| [readmission-alerting](cpu-ml-opus5/readmission-alerting/) | Hospital readmission risk alerting pipeline | 2/4 |
| [covertype-district-rollout](cpu-ml-opus5/covertype-district-rollout/) | Cover-type classifier repair for a district rollout | 1/4 |
| [asof-feature-leakage](cpu-ml-opus5/asof-feature-leakage/) | Point-in-time feature correctness in a financial ML pipeline | 0/4 |
| [post-pci-clinical-case-retrieval-drift](cpu-ml-opus5/post-pci-clinical-case-retrieval-drift/) | Clinical case-retrieval quality under distribution drift | 0/4 |

Four independent attempts per task; solved means reward ≥ 0.99 (covertype's
graded terminal also awards partial credit — its unsolved runs score up to
0.67). The spread is deliberate: the pack runs from tasks a frontier model
solves half the time down to tasks it cannot yet solve at all — **we build
tasks across the full difficulty range, calibrated to wherever a lab needs
the training signal to sit.**

## What's in each sample

- **`<sample>.pdf`** — the task write-up: scenario, difficulty design, and
  grading rationale. Read this first.
- **`task/`** — the complete task as served to the agent: environment
  (Dockerfile + workspace), instructions, gold and alternative solutions,
  the grader, and negative controls.
- **`traces/`** — full transcripts of agent attempts against the task,
  showing the long-horizon act → observe → correct behavior the tasks are
  designed to elicit.

## Design standard

Every task is built to the same bar: **long-horizon, forced discovery** (the
solution is only discoverable by acting in the environment, never disclosed
up front) and **sound, isolated grading** (a known-correct solution scores
1.0, a *different* correct solution also scores 1.0, and every shortcut or
forged result scores ~0, verified by negative controls).

---

Questions or partnership enquiries: **suleiman@tensium.co.uk** · [tensium.co.uk](https://tensium.co.uk)
