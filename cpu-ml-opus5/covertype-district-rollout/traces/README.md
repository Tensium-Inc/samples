# covertype-district-rollout — rollout traces

## Claude Opus 5, shipped build (headline calibration) — folder: opus5_hardened_build/
4 independent runs on the shipped build (recall bars 0.44 / 0.44 plus the balanced-accuracy
skill gate 0.27).
- rewards 1.00 / 0.67 / 0.67 / 0.37 · 1/4 strict passes (25%)
- Run 3 scored 1.00: full gated trajectory, both recall bars and the skill gate cleared.
  Runs 1 and 4 scored 0.67: full release and fault cleared, one recall bar short.
  Run 2 scored 0.37: validated clean, reached the 100 tool-call step budget before completing.
- Per rollout: transcript.json (full action log), reward.json, verifier_info.json, run.json,
  console.log; plus summary.json / summary.md.

## DeepSeek V4 Flash (lower-tier separation) — folder: flash_separation/
4 rollouts: rewards 0.57 / 0.52 / 0.52 / 0.40 · strict 0/4 · mean 0.502.
Flash never clears both bars; used as the stop-signal baseline. Measured on the pre-fix build.
