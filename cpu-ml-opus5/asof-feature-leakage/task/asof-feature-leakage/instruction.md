Our repeat-purchase backtest looks healthy, but production rankings become unreliable during periods with heavy returns. Two earlier fixes barely changed the result. Find the real point-in-time defect, repair it, and complete the deployment workflow.

Work in /workspace/target. The editable pipeline is in src/. You may inspect config.yaml, data/transactions.parquet, data/DATA_CARD.md, scripts/run_backtest.py, and tests/test_smoke.py. Internet access is unavailable and dependencies are installed.

Run tests with:

python3 -m unittest discover -s tests

Run the backtest with:

python3 scripts/run_backtest.py

Drive operations with python3 env_cli.py OP.

First run inspect, profile, and sample_records --cursor N for all thirteen requested cursors. Infer one allocation and point-in-time rule from the returned examples and the transaction panel.

Then follow this order:

reproduce
deploy
validate
diagnostics.read
recovery.apply --family FAMILY
validate
promote
submit

Use the recovery family reported by diagnostics.read. Validation is rate-limited and returns only clean or dirty. After recovery it checks deployed feature values on a hidden audit slice. Grading uses deployed code and ignores workspace result files.

Finish only after validation is clean and submit succeeds.
