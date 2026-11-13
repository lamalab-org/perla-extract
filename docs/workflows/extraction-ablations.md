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
pdf = "papers/10.1002--adfm.201904856.pdf"
supplement = "papers/10.1002--adfm.201904856-SI.pdf"
reference = "data/study_extraction/ground_truth/v2/dev/10.1002--adfm.201904856"
```

`[extraction]` applies to both arms; `[control]` and `[treatment]` change only the
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
