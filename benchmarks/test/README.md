# Human-corrected test set

**30 papers, 57 PDFs.** [papers.json](papers.json) binds each article/SI to the
existing [human reference release](../../data/study_extraction/ground_truth/v2/release.json).
Use these references for final evaluation after development on separate,
[model-labeled papers](../development).

## Evaluate frozen pipelines

Set `sources` in [study.toml](study.toml) to the original PDF folder. First freeze
the configurations on development data and carry all development costs/unknown
holds into the campaign budget.

```sh
perla-experiment benchmarks/test/study.toml --check
# After development is frozen and the PR reviewed; makes paid requests:
perla-experiment benchmarks/test/study.toml
# Continue interrupted work:
perla-experiment benchmarks/test/study.toml --resume
```

The recipe contains 90 primary comparisons and 30 repeatability runs on a fixed
10-paper subset, subject to the remaining $150 campaign budget. That subset is
for test repeatability, not calibration. Do not retune on these results.

Open `summary.md` first; `figure-data/important_fields.csv` contains field scores
and `scoring_diagnostics.csv` explains value/context failures. Shared baseline
attempts count once in campaign spending.

The archived release uses `dev` paths/metadata and these papers already informed
scoring/extraction changes. We preserve that history and designate their current
role as `test` in the roster; they cannot be described as untouched held-out data.
Coverage limitations remain visible. [Earlier pilot rescores](pilot-rescore.csv)
are historical diagnostics, not a new frozen test result.
