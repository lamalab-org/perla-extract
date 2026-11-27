# Run an experiment

## Choose the comparison

| Question | Start with |
| --- | --- |
| Does one draft review help? | `examples/experiment.toml`: direct extraction versus one refinement |
| How close are cheaper models to Astra? | [Model-labeled development set](https://github.com/lamalab-org/perla-extract/tree/main/benchmarks/development) |

**Refinement** re-reads the source and draft; measure its gain against its cost.
**Enrichment** produces separate composition/processing interpretations for export;
it does not rewrite the scored `extraction.json`. Both are off by default.

## Check, then run

From an [installed checkout](../getting-started.md#install), copy the one-paper
example to the repository root and use separate model-labeled development papers:

```sh
cp examples/experiment.toml my-experiment.toml
perla-experiment my-experiment.toml --check
perla-experiment my-experiment.toml
# If interrupted:
perla-experiment my-experiment.toml --resume
```

One `[[papers]]` block links `pdf`, optional `supplement`, and `reference`.
The reference directory contains `ground_truth.json` and `manifest.json` for both
sources. Paths are relative to the TOML; hashes must match the original PDFs.
For the committed dataset, set `dataset` and `sources` instead of listing papers.

Shared `[extraction]` and `[stages.default]` settings apply to every arm;
`[arms.control]` and `[arms.treatment]` hold their differences. Add more named
arms under `[arms.NAME]`; `baseline` selects the reference arm (default `control`).
Optional passes need a larger `max_model_calls` budget.
[Stage and context reference](extraction.md).

`--check` creates no output and makes no requests. Execution preflights every
paper before paid work. Resume verifies the frozen inputs, code and method;
it reuses sealed runs without retrying failed or uncertain attempts.
For another experiment, choose a new `output` directory.

## Read the result

| Output | Purpose |
| --- | --- |
| `summary.md` | Start here: paired effects, completion, costs and issues |
| `figure-data/important_fields.csv` | PCE, FF, Voc, Jsc and other key field counts |
| `figure-data/subfield_scores.csv` | Performance, identity, processing and other subfields |
| `figure-data/scoring_diagnostics.csv` | Per-field value, unit and attribution failures |
| `figure-data/runs.csv` | Requests, tokens, latency, cost and failures |
| `runs/paper-NNN/ARM-REPEAT/` | Extraction, evaluation, report and local events |
| `comparison.json` | Campaign accounting and named paired comparisons |
| `errors.json` | Execution failures, retained once per attempt |
| `comparisons/CANDIDATE-vs-BASELINE/primary/` | Paired summary, comparison, intervals and field tables |

A change of `+0.03` means three percentage points of attributed fact F1.
Paired summaries live under `comparisons/` for every experiment, including two-arm
studies. Paper means receive equal weight; bootstrap intervals resample papers, not facts
or repeats. Field metrics retain literal and malformed predictions.

`reference_agreement` measures agreement with the annotated subset; incomplete
references cannot establish absolute precision. Missing/failed runs remain visible.
Empty values mean unavailable, not zero. Cost projections include failures and
require complete accounting. See [scientific methods](../methods/validation-and-scale.md)
and the [offline scoring example](scoring-example.md) for details.

Tune on model-generated development references. After freezing settings, use the
[human test recipe](https://github.com/lamalab-org/perla-extract/tree/main/benchmarks/test).
The human test repeatability subset must not be used to calibrate settings.
