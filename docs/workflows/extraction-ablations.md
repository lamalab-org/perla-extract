# Compare extraction choices

An ablation is an ordinary extraction run with an explicit setting changed. There is
no separate experiment planner or generated shell program. Save the method as TOML,
declare the cohort and arm settings in a small study JSON, run both arms on the same
PDFs, score against the same frozen references, then list the attempts in a CSV.
Keep failed attempts in that CSV.

## Start with a complete offline comparison

Run the [synthetic example](scoring-example.md) first. One command creates the
reviewed reference, scores, paired CSV roster, comparison report and figure tables.
Its candidates are manually edited, so it teaches the reporting path without
claiming to test a model or extraction setting.

## Activate the extraction environment

The offline example's `.venv-scoring` contains no extraction command or PDF parser.
For actual extraction, follow the [checkout installation](../getting-started.md#install)
and activate `.venv-extraction` from the repository root:

```bash
source .venv-extraction/bin/activate
perla-extract --help
git rev-parse HEAD
```

Configure the selected provider's credentials, or the server settings described in
[open models](open-models.md). Keep the source revision with the results. The commands
below use the same parser and resource limits in both arms.

## Choose a question

| Question | Treatment file | Factor to declare in the study JSON |
| --- | --- | --- |
| Does a second source reading help? | `examples/ablations/01-one-reading.toml` | `claim_recall_passes` |
| Does full-draft revision help? | `examples/ablations/02-no-refinement.toml` | `use_refinement` |
| Do intermediate source statements help? | `examples/ablations/03-direct-extraction.toml` | `use_claim_ledger` |
| Can another model read the evidence? | `examples/ablations/04-reader-deployment.toml` | `deployments.read.model` and each changed deployment field |

The supplied files change only the named choice relative to the application defaults.
They are starting points, not claims that a treatment is better. Copy them and record
the entire intended method before a real study. See [extraction](extraction.md) for
what each stage does and [open models](open-models.md) for endpoint configuration.

Compare source reading, full-draft revision, and model substitution separately first.
A direct-source arm changes the evidence representation as well as removing model
calls; interpret it as that combined intervention. It still receives all retained
source blocks. Repair only targets audit-visible gaps, so it cannot establish that
unflagged information is complete. Enrichment interprets reported composition and
processing; its downstream value requires a separate evaluation from core extraction.

## Run and score one pair

Choose a paper ID present in the frozen release. Replace `PAPER_ID` and the source
paths below with that paper's actual main PDF and SI. Check that they cover the
same sources as its reference; the comparison rejects mismatched source hashes.
An output folder must be new or empty. Dry runs also consume their output folder.

Copy `examples/ablations/study.json` to `study.json` and replace `PAPER_ID`. Before
running a cohort, list **all** its paper IDs and set the intended repeat count:

```json
{
  "paper_ids": ["PAPER_ID"],
  "repeats": 1,
  "factors": {
    "claim_recall_passes": {"control": 2, "treatment": 1}
  }
}
```

Repetitions are numbered `1` through `repeats` for every paper and arm. Factor names
are the dotted paths in saved scientific settings, such as `deployments.read.model`.
For a refinement ablation, declare `use_refinement` with the intended boolean values
instead. Declare every deliberately changed setting, including endpoint or decoding
changes. Other settings must agree between arms, and each arm must use the same
settings across papers and repeats. Use separate comparisons for different methods.

Commit the study file with the arm TOMLs, fixed scorer/reference choices and retry
policy before collecting results. Randomize or counterbalance execution order.
The comparator stores the study contents and hash; it cannot establish when you
wrote them or detect attempts you never listed. Do not regenerate the intended
cohort from the successful runs after execution.

The baseline enables two source-reading passes per window, assembly, full-draft
revision, targeted repair and enrichment. The treatment changes only the number of
source-reading passes. These commands select PyMuPDF explicitly for both arms;
the parser choice is recorded and should stay fixed for this experiment.

Check the inputs before sending a model request:

```bash
perla-extract --pdf papers/PAPER_ID.pdf --supplement papers/PAPER_ID-SI.pdf \
  --parser pymupdf --output-dir plans/PAPER_ID/control-1 --dry-run
```

Inspect the planned calls and input estimates in that plan's `report.json`. Then
run both arms with separate model caches and explicit request/spend limits:

```bash
perla-extract --pdf papers/PAPER_ID.pdf --supplement papers/PAPER_ID-SI.pdf \
  --parser pymupdf --max-model-calls 14 --max-cost-usd 2.00 \
  --model-cache-dir caches/PAPER_ID/control-1 \
  --output-dir runs/PAPER_ID/control-1

perla-extract --config examples/ablations/01-one-reading.toml \
  --pdf papers/PAPER_ID.pdf --supplement papers/PAPER_ID-SI.pdf \
  --parser pymupdf --max-model-calls 14 --max-cost-usd 2.00 \
  --model-cache-dir caches/PAPER_ID/treatment-1 \
  --output-dir runs/PAPER_ID/treatment-1

perla-evaluate --truth data/study_extraction/ground_truth/v2/dev/PAPER_ID \
  --prediction runs/PAPER_ID/control-1 \
  --output runs/PAPER_ID/control-1/evaluation.json --fail-on-scoring-issues

perla-evaluate --truth data/study_extraction/ground_truth/v2/dev/PAPER_ID \
  --prediction runs/PAPER_ID/treatment-1 \
  --output runs/PAPER_ID/treatment-1/evaluation.json --fail-on-scoring-issues
```

These budgets are starting limits, not a guarantee that a paper will finish or a
hard billing ceiling. Keep a budget-limited attempt in the roster. For a self-hosted
model with no known dollar cost, use token/request/time limits instead of
`--max-cost-usd`; see [resource limits](open-models.md#understand-context-and-resource-limits).

A model cache hit reproduces a saved answer; it is not another stochastic repetition.
The separate caches above prevent one arm from reusing the other's paid requests.
For repeat 2, choose new output and cache directories for both arms.
For independent repeated generations, use a fresh `--model-cache-dir` for every
attempt. Record the server revision, decoding settings and cache policy. Temperature
zero and a fixed seed do not guarantee identical results on every backend.

Create `runs.csv` with paths relative to its own directory:

```csv
paper_id,arm,repeat,run_dir
PAPER_ID,control,1,runs/PAPER_ID/control-1
PAPER_ID,treatment,1,runs/PAPER_ID/treatment-1
```

A failed attempt followed by a successful rerun is additional resource use, not a
new paired quality repetition. Add the optional `role` column to retain it:

```csv
paper_id,arm,repeat,run_dir,role
PAPER_ID,control,1,runs/PAPER_ID/control-1,paired
PAPER_ID,treatment,1,runs/PAPER_ID/treatment-1,paired
PAPER_ID,treatment,,runs/PAPER_ID/failed-treatment,accounting_only
```

Accounting-only rows need no score and must belong to a paper/arm in the paired
roster. Costs include every listed distinct attempt; missing accounting prevents a
complete cost total but does not discard otherwise valid paired scores. Unlisted
attempts cannot be inferred. The cohort audit lists all retained attempt folders.

Then compare:

```bash
perla-compare runs.csv --study study.json \
  --output comparison.json --tables-dir figure-data
```

This verifies run manifests, predictions, references, scorer settings and paid-work
accounting. The required study file detects missing or extra paper/repeat pairs,
reversed arms and changed factor values. Additional or missing method settings and
drift within an arm also block the aggregate quality result. An empty `factors`
object permits only identical method settings, for example two independent repeats
of the same method. There is no unchecked comparison mode.

Rejected comparisons retain diagnostics and available per-run accounting. An
incomplete cohort cannot produce a complete cohort cost or a cost-saving fraction.
`planned_runs`, `listed_paired_runs`, `paired_complete_runs` and `paired_scored_runs`
make coverage explicit. `completion_fraction` measures completed paired runs only
when the roster matches the declared cohort; it is not a scientific accuracy score.

For a cohort, use `perla-extract-cohort --config method.toml` with the roster and
source directories described in [batch extraction](quality-first-ground-truth.md).
The cohort audit records each selected run directory. Resume checks source bytes,
parser and pipeline implementation, runtime dependencies, effective settings, schema, prompts, and
sealed valid outputs. A rerun creates a numbered attempt and retains previous work.

## Read the result and make figures

The primary difference is treatment minus control in per-paper, populated-group
macro F1. Repeat differences are averaged within each paper; papers then receive
equal weight. The percentile bootstrap resamples papers. Its interval is conditional
on the observed repeats; it does not estimate all possible model or dataset variation.
One paper has no paper-bootstrap interval. No interval is produced from missing or
incompatible quality results.

`--tables-dir` creates plain, inspectable files:

| File | Use |
| --- | --- |
| `runs.csv` | Plot individual quality, token use, request count, latency and recorded spend; includes failed/missing results |
| `group_scores.csv` | Inspect strict, value-only and attribution precision/recall/F1 with their truth/predicted/matched counts |
| `paired_papers.csv` | Plot paired paper differences and the number of observed repeats |
| `analysis.json` | Preserve the estimand, roster hash, factor checks, interval method, seed and diagnostics |

Empty CSV cells mean unavailable, not zero. `known_total_tokens` retains the observed
subtotal; token totals are populated only when token accounting is explicitly complete. Known spend remains available even if a
prediction or score is missing. Total recorded cost and a saving fraction require complete
accounting. `cost_sources` identifies the adapter provenance; complete availability
of those amounts does not certify that they equal provider invoices. Self-hosted tokens and latency are useful resources; provider-dollar
fields are not GPU cost, energy consumption, or total ownership cost. Show cache
hits and failed attempts alongside comparisons.

For a paper, show paired differences, group-level quality, and quality versus measured
resources with uncertainty. Keep development selection separate from the final unseen
paper set. Do not treat individual extracted facts or repeated generations as
independent papers. This software checks consistency and provenance; source-checked
references and expert validation of scorer decisions remain necessary.
