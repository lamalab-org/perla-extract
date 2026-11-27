# Experiment records and manual comparison

For the normal starting path, use [Run an experiment](../workflows/extraction-ablations.md). The experiment command creates these records automatically. This page is for inspecting them or comparing runs executed outside that command.

For `perla-experiment`, read the root `summary.md` first for campaign coverage,
cost and links to each paired comparison. The paired summaries under
`comparisons/CANDIDATE-vs-BASELINE/primary/` explain the quality difference,
uncertainty and per-run diagnostics. Each summary displays its corresponding
`comparison.json`; it does not recompute scores.

## Compare externally executed runs

Create a study declaration before collecting results and list all attempts in a roster. Run settings, source scope, scorer configuration and references must remain fixed except for the declared intervention.

```json
{
  "paper_ids": ["PAPER_ID"],
  "repeats": 1,
  "factors": {"claim_recall_passes": {"control": 1, "treatment": 2}}
}
```

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
Both `complete` and `complete_needs_review` mean execution completed. Citation
warnings remain visible without making a run partial. Structural or unclassified
validation errors still block the scored comparison, independently of completion.

For a cohort, use `perla-extract-cohort --config method.toml` with the roster and
source directories described in [batch extraction](../workflows/quality-first-ground-truth.md).
The cohort audit records each selected run directory. Resume checks source bytes,
parser and pipeline implementation, runtime dependencies, effective settings, schema, prompts, and
sealed valid outputs. A rerun creates a numbered attempt and retains previous work.

## Read the result and make figures

The primary difference is treatment minus control in per-paper attributed fact
micro F1. Repeat differences are averaged within each paper; papers then receive
equal weight. The percentile bootstrap resamples papers. Its interval is conditional
on the observed repeats; it does not estimate all possible model or dataset variation.
One paper has no paper-bootstrap interval. No interval is produced from missing or
incompatible quality results.

`--tables-dir` creates plain, inspectable files:

| File | Use |
| --- | --- |
| `runs.csv` | Plot individual quality, token use, request count, latency and recorded spend; includes failed/missing results |
| `group_scores.csv` | Inspect attributed and recovery precision/recall/F1 with their truth/predicted/matched counts |
| `paired_papers.csv` | Plot paired paper differences and the number of observed repeats |
| `paired_reference_agreement.csv` | Diagnostic paired differences, contributing repetitions, and complete/subset status |
| `analysis.json` | Preserve the estimand, roster hash, factor checks, interval method, seed and diagnostics |

Each score row includes `interpretation`: unreviewed reference scope is diagnostic
`reference_agreement`. It cannot supply a verified quality comparison.

The `reference_agreement` object in `comparison.json` computes a diagnostic
contrast without per-run review. All source, run, reference, scorer and declared
setting checks still apply. `complete_roster` includes every planned pair;
`conditional_on_complete_pairs` explicitly lists omitted pairs and describes only
the observed complete subset. `blocked` has no effect estimate. Facts with
unresolved attribution retain zero credit and remain in the denominator. The
separate `quality` result still requires reviewed benchmark scope and resolved
alignment. The CLI succeeds for a complete diagnostic comparison; add
`--require-reviewed-quality` when that stricter gate is needed.

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
