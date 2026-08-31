# Flash calibration — the recovery-family fix, cheap tier first

Run before any frontier spend, per review. The pass signal is the GUESS COUNT, not the reward.

    rollout 0   reward 0.52   13 actions   0 family guesses
    rollout 1   reward 0.40   23 actions   0 family guesses
    rollout 2   reward 0.52   14 actions   0 family guesses
    rollout 3   reward 0.57   23 actions   0 family guesses

    0 guesses across 73 actions.

Against the pre-fix set in _private/pass4_prefamilyfix/: 61 guesses across 138 actions, one
rollout spending 49 of its 62 actions enumerating synonyms for "rebuild the cache".

All four cleared the forced recovery on the first attempt. Flash: 0/4 strict, mean 0.502 --
inside the 0.40-0.75 band, four valid failures.
