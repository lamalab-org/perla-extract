# Run the scoring example

This example contains **invented source text and simulated review actions**. It tests
the real correction → export → scoring path without PDFs, credentials, network calls,
or private reviewer feedback. It is not a measurement of model extraction accuracy.

## Run from a checkout

Use the code revision accompanying the result you want to reproduce. The example
has a pinned Python 3.12 reference environment for Linux/macOS, without PDF parsers
or model-provider SDKs. From that checkout:

```bash
python3.12 -m venv .venv-scoring
source .venv-scoring/bin/activate
python -m pip install -r examples/scoring/requirements.txt
git rev-parse HEAD
PYTHONPATH=.:src python -m examples.scoring.run --output /tmp/perla-scoring-example
```

Choose a new output directory for each run. An existing directory is refused to
protect review history. The result should say `PASS: 5 synthetic cases`.
The same command also writes `study.json`, `runs.csv`, `comparison.json`, and plotting CSVs in
`figure-data/` through the normal comparison command. No extra command or roster
editing is needed for this first demonstration.
The script records Python and key package versions in `summary.json`; retain the
checkout revision and environment with a published result. The normal package's
dependency ranges are separate from this pinned example environment.

## What happens

The synthetic paragraph reports a finished ITO/perovskite/Ag stack, a champion
reverse-scan PCE of 20%, and FF of 80%. It also mentions DMF as a processing solvent
and a discarded estimate of 21%. The seed incorrectly puts DMF in the stack, uses
21% as the final PCE and omits FF.

The script saves three corrections through the normal review store, completes the
simulated review stages, and exports a format-4 reference. It then scores five
unchanged candidates against that reference:

| Candidate | Correct stack / predicted / reference | Attributed performance / predicted / reference | Recovered performance |
| --- | --- | --- | --- |
| Original seed | 0 / 1 / 1 | 0 / 1 / 2 | 0 |
| Corrected extraction | 1 / 1 / 1 | 2 / 2 / 2 | 2 |
| PCE expressed as an explicit fraction, 0.20 | 1 / 1 / 1 | 2 / 2 / 2 | 2 |
| Correct values assigned to a forward scan | 1 / 1 / 1 | 0 / 2 / 2 | 2 |
| Raw PCE `20%`, parsed number `2` | 1 / 1 / 1 | 1 / 2 / 2 | 1 |

The stack is deliberately represented by one raw stack string to exercise the
fallback for absent structured layers. It therefore counts as one stack fact,
not one fact per material. The performance facts are PCE and FF. Other scientific
groups are empty, not counted as successes.
Recovery counts compatible values without establishing ownership: the wrong-scan
case still recovers both values while receiving no attributed performance credit.

All candidates can pass literal evidence grounding: even 21% occurs in the source.
The wrong-scan and wrong-value cases illustrate why citation validity is not
scientific correctness. The unit-equivalent candidate uses the same source document.
The fifth case preserves the correct source wording but corrupts its convenience
number. Source grounding still finds both PCE and FF; quantity validation reports
one contradiction. PCE remains counted but loses credit, while FF stays correct.

Each candidate is also exported to NOMAD. The explicit fractional PCE becomes 20%,
and the contradictory PCE is withheld rather than exported as 2% or silently repaired.
The raw metric and its interpretation remain in archive notes. `summary.json` checks
these outcomes against independently written expectations as well as score counts.

## Inspect the outputs

```text
perla-scoring-example/
  summary.json                      # Actual/expected counts and environment
  study.json                        # Expected paper, repeat and synthetic candidate labels
  runs.csv                          # Seed/control and corrected/treatment paths
  comparison.json                   # Paired result and input checks
  figure-data/                      # runs.csv, group_scores.csv, subfield_scores.csv, paired_papers.csv, analysis.json
  review/                           # Simulated saved review state
  truth/dev/10.0000--synthetic/
    ground_truth.json                # Corrected reference
    seed_extraction.json             # Unchanged erroneous seed
    review_events.json               # Corrections and simulated decisions
    manifest.json                    # Exported revision and content hashes
    document.json                    # Additional synthetic evidence document
  predictions/<candidate>/
    extraction.json
    document.json
    report.json                       # No-model-call accounting
    evaluation.json                   # Full scorer output, including field paths
    nomad_export.json                 # Scalar projection, retained raw values and issues
```

The example uses the normal four-file export and adds its invented evidence
document alongside it. `report.json` reports zero model
calls and cost; elapsed time measures local fixture writing, not model latency.
Review timestamps and hashes vary between runs. The fact counts and scoring settings
must stay the same. The tests compare them with separately written expectations in
`examples/scoring/expected.json`.

For a failure, start with `summary.json`: compare `actual` with `expected` for credit,
then `checks` with `expected_checks` for grounding, conflicts and scalar export.
Open the corresponding prediction's `evaluation.json` for affected paths and
`nomad_export.json` for retained raw values. A higher score is not the test's goal;
the declared scientific behavior must agree in every case.

Re-run the ordinary scorer on the generated seed:

```bash
PYTHONPATH=.:src python -m perla_extract.study_extraction.evaluation_cli \
  --truth /tmp/perla-scoring-example/truth/dev/10.0000--synthetic \
  --prediction /tmp/perla-scoring-example/predictions/seed \
  --output /tmp/perla-scoring-example/seed-score.json \
  --fail-on-scoring-issues
```

To use actual expert corrections, follow [From corrections to a benchmark](
review-to-benchmark.md). Do not substitute this example's simulated approvals for
source review, or include synthetic cases in real-paper benchmark aggregates.

## Inspect the paired comparison

`runs.csv` pairs the seed as `control` with the corrected prediction as `treatment`.
These are deliberately edited candidates with the same synthetic source, not two
model deployments or an ablation. `comparison.json` should have
`quality_status: "ready"`, no issues, and `mean_paired_fact_f1_delta: 1.0`.
The one-paper interval has status `insufficient_papers` and empty bounds. Both
arms have zero model calls; their difference is not a measurement of an extraction
improvement or cost saving.

Open `figure-data/paired_papers.csv` to inspect the difference and
`figure-data/group_scores.csv` to trace it to counts.
`figure-data/subfield_scores.csv` gives the finer breakdown from the same ledger. The comparison checks that
both runs bind the same source bytes and evidence spans. Here the invented evidence
JSON is the source; real extraction runs fingerprint their PDFs instead.

To repeat only comparison, using fresh destinations:

```bash
PYTHONPATH=.:src python -m perla_extract.study_extraction.comparison \
  /tmp/perla-scoring-example/runs.csv \
  --study /tmp/perla-scoring-example/study.json \
  --output /tmp/perla-scoring-example/comparison-again.json \
  --tables-dir /tmp/perla-scoring-example/figure-data-again
```

Proceed to [controlled extraction experiments](extraction-ablations.md) for real
PDFs and models. That step requires choosing the source/reference pair, deployment,
method, budget and repetitions; the offline example does not make those choices.
Its study factor `synthetic_candidate` identifies manual fixtures, not a model setting.
