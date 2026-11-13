# Run an experiment

Edit one file. The command runs both arms, scores them against the references,
and saves the comparison and plotting tables.

## 1. Set the question and papers

From an [installed checkout](../getting-started.md#install):

```bash
cp examples/experiment.toml experiment.toml
```

Open `experiment.toml`. Set the model, PDF/SI and reference paths. The template asks
whether a second source reading helps:

```toml
output = "results/readings"
repeats = 1

[extraction]
model = "openai/gpt-5.2"
parser = "pymupdf"
max_model_calls = 14

[control]
claim_recall_passes = 1

[treatment]
claim_recall_passes = 2

[[papers]]
pdf = "papers/PAPER_ID.pdf"
supplement = "papers/PAPER_ID-SI.pdf"
reference = "data/study_extraction/ground_truth/v2/dev/PAPER_ID"
```

`[extraction]` applies to both arms; `[control]` and `[treatment]` change only the
settings written there. Add another `[[papers]]` block for another paper. Remove
`supplement` when there is no SI. Paths are relative to this file. Each reference
must be finalized and identify the same PDF/SI bytes.

## 2. Check, then run

With your [provider credentials configured](open-models.md):

```bash
perla-experiment experiment.toml --check
perla-experiment experiment.toml
```

The check prints the exact changes and total run count. It validates every input
and configuration without parsing PDFs, creating outputs or calling a model.
The second command makes model calls. `max_model_calls` limits requests **per run**;
other [resource limits](open-models.md#understand-context-and-resource-limits) use the
same settings as extraction.

## 3. Inspect the result

| Output | What to open it for |
| --- | --- |
| `comparison.json` | Paired quality difference, uncertainty and resource accounting |
| `figure-data/` | CSVs ready for plotting |
| `runs/` | Individual extractions, scores, evidence and logs |
| `errors.json` | Attempts that need attention |

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
