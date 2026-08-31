You are taking over a live retrieval incident from the clinical review team.

The service supports a pre-outcome review of hypotension after PCI. It still returns twelve cases that look reasonable, which is why this problem escaped the usual smoke checks. The result is nevertheless wrong in production. The files that landed in storage are not exactly the population approved for release, a displayed PMCID is not always the serving identity, and the panel changes under ordinary request and inventory variations.

Please repair the service and take the incident all the way through submission. I need a working terminal state, not a written report.

Everything you need is in `/workspace/target/`. The editable Python package is under `src/`. The corpus and incident material are under `data/`, including the PDF, Word handoff, HTML contract, JSON and CSV records, source manifest, and release registry. There is no internet access. You may inspect files, write small experiments, and run the candidate as often as useful.

The public retrieval command is:

```bash
python3 -m src.cli --artifact-root data --request data/query_facets.json
```

The operations service is `env_cli.py`. It remembers what has already happened, so follow the incident in order. If you skip a step, the service will reject it.

Start by collecting the six observations below. Do not edit the candidate until you understand what the landing data, release data, query, and current output are telling you.

```bash
python3 env_cli.py inspect.sources
python3 env_cli.py inspect.schema
python3 env_cli.py audit.query
python3 env_cli.py profile.corpus
python3 env_cli.py sample.records
python3 env_cli.py test.visible
```

After that, reproduce the incident, deploy the current candidate, and inspect the forced runtime failure:

```bash
python3 env_cli.py reproduce
python3 env_cli.py deploy
python3 env_cli.py validate.runtime
python3 env_cli.py diagnostics.read
```

Use the diagnosed family in the recovery command:

```bash
python3 env_cli.py recovery.apply --family <diagnosed-family>
python3 env_cli.py validate.runtime
```

Choose the recovery family carefully. A wrong recovery locks the incident and cannot be undone.

Once runtime health is restored, gather the release evidence in this order:

```bash
python3 env_cli.py audit.provenance
python3 env_cli.py audit.lexical
python3 env_cli.py audit.fusion
```

Now repair `src/`. The evidence from these audits is the release authority. Do not assume that a familiar raw-similarity retriever is correct simply because its public results look plausible. When the population, canonical identity, eligible text, within-domain ranking, and fusion behavior agree with the evidence, commit that version:

```bash
python3 env_cli.py deploy.ranker
python3 env_cli.py validate.ranker
```

Only a clean ranker validation opens the panel-allocation evidence:

```bash
python3 env_cli.py audit.balance
```

Read that receipt as observed behavior, not as pseudocode. Infer the request-dependent family cycle from the normalized request evidence, byte totals, request sizes, and emitted prefixes. The allocator must remain balanced when the requested panel size changes.

Before the final deployment, you have exactly two shadow checks. Use each probe once, in either order:

```bash
python3 env_cli.py shadow.challenge --probe allocator
python3 env_cli.py shadow.challenge --probe provenance
```

Each check gives only a stable or drift result, a rough mismatch size, and the affected dimension. It will not reveal the expected PMCIDs. You may edit `src/` between the two checks if the first result changes your diagnosis. Repeating a probe is rejected.

Treat validation calls as commitments. The whole incident has a budget of four: two runtime validations, one ranker validation, and one final validation. There is no spare semantic retry. A dirty semantic result may route the workflow back to deployment, but the validation budget will already be exhausted.

When you are satisfied that the implementation follows the release behavior, freeze the final candidate and complete the remaining operations:

```bash
python3 env_cli.py deploy.final
python3 env_cli.py validate.final
python3 env_cli.py shadow.replay
python3 env_cli.py cutover.check
python3 env_cli.py cutover
python3 env_cli.py promote
python3 env_cli.py submit
```

The visible smoke test is only a basic sanity check. The released candidate must also survive realistic variations: query paraphrases, different request sizes, reordered manifest entries, rewritten post-adjudication or display metadata, unlisted landing-file noise, and serving projections where the stable `record_key` remains but a landing display PMCID does not. Panel membership, order, scores, population counts, and authoritative incident state all matter.

Do not try to replace the workflow with a one-shot code drop or a claimed result file. The service records the real trajectory. Work from the evidence, make deliberate commitments, and leave the incident in the submitted state.
