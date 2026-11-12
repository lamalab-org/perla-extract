# Compare extraction choices

An ablation is an ordinary extraction run with an explicit setting changed. There is
no separate experiment planner or generated shell program. Save the method as TOML,
run both arms on the same PDFs, score against the same frozen references, then list
the attempts in a CSV. Keep failed attempts in that CSV.

## Choose a question

| Question | Treatment file | Setting to declare with `--factor` |
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

From an installed checkout, choose a paper ID present in the frozen release. Replace
`PAPER_ID` and the source paths below with real inputs. An output folder must be new
or empty. Dry runs also consume their output folder; use a separate folder for them.

```bash
perla-extract --pdf papers/PAPER_ID.pdf --supplement papers/PAPER_ID-SI.pdf \
  --output-dir runs/PAPER_ID/control-1

perla-extract --config examples/ablations/01-one-reading.toml \
  --pdf papers/PAPER_ID.pdf --supplement papers/PAPER_ID-SI.pdf \
  --output-dir runs/PAPER_ID/treatment-1

perla-evaluate --truth data/study_extraction/ground_truth/v2/dev/PAPER_ID \
  --prediction runs/PAPER_ID/control-1 \
  --output runs/PAPER_ID/control-1/evaluation.json --fail-on-scoring-issues

perla-evaluate --truth data/study_extraction/ground_truth/v2/dev/PAPER_ID \
  --prediction runs/PAPER_ID/treatment-1 \
  --output runs/PAPER_ID/treatment-1/evaluation.json --fail-on-scoring-issues
```

A model cache hit reproduces a saved answer; it is not another stochastic repetition.
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
perla-compare runs.csv --factor claim_recall_passes \
  --output comparison.json --tables-dir figure-data
```

This verifies run manifests, predictions, references, scorer settings and paid-work
accounting. The factor check rejects other changed method settings. List additional
`--factor` values only when the experiment deliberately changes those settings.
Without a factor declaration, differences are reported but not restricted.

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
