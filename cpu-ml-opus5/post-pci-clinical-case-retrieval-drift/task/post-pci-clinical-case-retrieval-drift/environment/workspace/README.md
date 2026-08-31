# Clinical retrieval incident workspace

This is the complete editable incident workspace. The candidate implementation is in `src/`, the public runner is `scripts/run_retrieval.py`, and the coarse visible smoke is `tests/test_visible.py`.

`data/` contains the real PMC case corpus plus the pre-outcome index case and operational artifacts in PDF, Word, HTML, JSON, and CSV formats. The landing directory intentionally contains one unlisted duplicate delivery file. The release population and the serving-time evidence boundary must be established from the incident materials and broker observations rather than guessed from file names.

The gated audits release behavioral evidence only. Static artifacts intentionally do not contain the exact request-phase or canonical identity-join implementation. Local experiments against the real corpus are allowed, but the full hidden terminal is not offline-verifiable. Two distinct, rate-limited shadow challenges provide coarse drift feedback before final deployment; their candidate subprocesses cannot write files or use the network.

Do not create a replacement score, receipt, or terminal file. Deployment snapshots and the final terminal are owned and recomputed by the environment.
