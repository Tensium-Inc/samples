Our green-taxi duration model is stable offline, but production requests lose location, fare, and local-time context. The service stays up and returns plausible predictions, so ordinary smoke checks have not found the defect. Repair the serving feature path and complete the release workflow.

Work in /workspace/target. Internet access is unavailable. The editable pipeline is in src/; real TLC records and public lookup tables are under data/.

Run visible checks with python3 -m unittest discover -s tests and the offline backtest with python3 scripts/run_backtest.py.

Drive the environment using python3 env_cli.py OP. Start with inspect, profile, and every sequential sample_records --cursor N requested by the service. Infer the serving contract from those observations together with the shipped data and its documentation under data/. The worked examples are consistent with the contract but do not by themselves pin every rule; where an example is silent, the lookup tables and the data card are authoritative.

Then drive reproduce, deploy, validate, diagnostics.read, recovery.apply --family FAMILY, validate, promote, and submit. Use the recovery family returned by diagnostics. Validation is permanently rate-limited; it returns clean or dirty, and a dirty result also reports how many of the contract columns do not yet conform, never which ones. Its allowance never resets during a run. A redeploy creates a new generation but does not restore spent validation attempts.

The deliverable is the corrected deployed pipeline plus a successful gated trajectory ending in submit. Do not stop at a code patch or report.
