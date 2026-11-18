# Run an experiment

Edit one file. The command runs both arms, scores them against the references,
and saves the comparison and plotting tables.

## 1. Set the question and papers

From an [installed checkout](../getting-started.md#install), open `experiment.toml`.
It already lists the 30 included development references. Set the model and PDF/SI
paths; keep only the paper blocks you intend to include before starting. The
single-paper template in `examples/experiment.toml` uses the same structure:

```toml
output = "results/readings"
repeats = 1

[stages.default]
model = "openai/gpt-5.2"

[extraction]
parser = "pymupdf"
max_model_calls = 14

[control]
claim_recall_passes = 1

[treatment]
claim_recall_passes = 2

[[papers]]
pdf = "papers/10.1002--adfm.201904856.pdf"
supplement = "papers/10.1002--adfm.201904856-SI.pdf"
reference = "data/study_extraction/ground_truth/v2/dev/10.1002--adfm.201904856"
```

`[stages.default]` selects the shared deployment. `[extraction]` applies to both arms; `[control]` and `[treatment]` change only the
settings written there.

**One `[[papers]]` block is one explicit match:**

| Field | What belongs here |
| --- | --- |
| `pdf` | Main article PDF |
| `supplement` | That article's SI PDF; omit only when the reference has no SI |
| `reference` | Reviewed ground-truth directory containing `ground_truth.json` and `manifest.json` for the combined paper and SI |

Add one block per paper. Paths are relative to the TOML file; filenames do not
need to match each other. The fields assign the main/SI roles. The reference's
manifest supplies the paper ID and source fingerprints; checks verify the source
file set, not their roles. The example uses a real included reference, but the
original PDFs are not distributed with it. Use those original files, not a
re-saved copy, and do not change the reference hashes to bypass a mismatch.

## 2. Check, then run

With your [provider credentials configured](open-models.md):

```bash
perla-experiment experiment.toml --check
perla-experiment experiment.toml
```

The check shows every paper → SI → ground-truth mapping, the exact changes and total run count. It validates every input
and configuration without parsing PDFs, creating outputs or calling a model.
The second command makes model calls. `max_model_calls` limits requests **per run**;
other [resource limits](open-models.md#understand-context-and-resource-limits) use the
same settings as extraction.

## 3. Inspect the result

**Open `summary.md` first.** It shows the tested change, paired quality difference,
uncertainty, scored/planned run counts, recorded spend and any blocking issues.

| Output | What to open it for |
| --- | --- |
| `figure-data/paired_papers.csv` | Per-paper effect: positive favors treatment, negative favors control |
| `figure-data/group_scores.csv` | Attributed and recovery precision/recall/F1 and fact counts by scientific group |
| `figure-data/subfield_scores.csv` | The same scores split into measurement, composition, processing and other subfields |
| `figure-data/runs.csv` | Per-attempt quality, tokens, requests, elapsed time, recorded cost and failures |
| `figure-data/cost_projection.csv` | All listed spend per completed extraction and simple projections for 10,000/50,000 outputs; unavailable when costs are unknown |
| `runs/paper-NNN/ARM-REPEAT/evaluation.json` | Trace scores to matched and unmatched fields in that run's `extraction.json` |
| `errors.json`, per-run `report.json` and `events.jsonl` | Diagnose failed attempts |
| `comparison.json` | Full machine-readable result and consistency checks |

The primary result is treatment minus control in attributed fact micro F1: `+0.03`
means **three percentage points**, not a 3% relative improvement. Each paper's F1
pools matched, predicted and reference fact counts across groups. Repeats are averaged
within each paper and papers receive equal weight. The 95% bootstrap interval
resamples papers; an interval spanning zero does not establish a direction. One
paper has no paper-bootstrap interval.

`quality_status = "needs_attention"` means the checks prevent a verified aggregate
conclusion. This includes incomplete reference coverage review, ambiguous attribution,
and missing, failed or inconsistent runs. Per-run scores remain available as
diagnostics. `interpretation = "reference_agreement"` means agreement with the
annotated subset: precision and F1 can penalize correct extractions absent from the
reference. Do not interpret these as established extraction accuracy. Retain failed
attempts rather than interpreting only the successful subset. Empty CSV
cells and JSON `null` mean unavailable, not zero. Missing cost can coexist with a
valid quality comparison; a known cost subtotal is not a complete total.

For corpus planning, the summary divides all listed spend, including failed
attempts, by the number of completed paired extractions. It also projects that
observed rate to 10,000 and 50,000 completed outputs. These are simple estimates
assuming the same paper mix, rates and failures; they exclude infrastructure and
unreported GPU costs. A completed output can still contain scientific errors.
Unknown costs or no completed outputs leave these estimates unavailable.

Run folders and response caches are separate for every paper, arm and repeat.
Execution order is shuffled reproducibly. The intended cohort and arm settings are
saved before execution; missing runs or inconsistent settings block the aggregate.
An existing output directory is refused. Change `output` to start a new experiment.

To ask a different question, change the setting in the two arm blocks—for example,
`use_refinement = false` versus `true`. [Stage descriptions](extraction.md) explain
what each switch does; [open models](open-models.md) covers stage-specific endpoints.

Freeze this file before collecting results. The included reference papers are
**development data**; final scientific claims need independent expert checks and
untouched papers. See [scientific validation](../methods/validation-and-scale.md).
[Generated records and manual comparison](../reference/experiment-artifacts.md)
are documented separately. No-model rehearsal is available in the
[offline scoring example](scoring-example.md).
