# Reusable development set

**30 papers, 57 original PDFs, existing human-adjudicated references.**
[papers.json](papers.json) fixes each article/SI role, SHA256, DOI and a
10-paper repeatability subset. It pins the already committed
[reference release](../../data/study_extraction/ground_truth/v2/release.json);
answers are not copied into another ground-truth format. These model-assisted,
human-finalized references have informed development; they are not a held-out test.

From the repository root after installation, verify the references and your original PDFs:

```sh
python -m examples.benchmarks.development --sources /path/to/papers
```

Use the filenames in the manifest. Articles and SIs may share that folder.
The check rejects missing, modified or swapped files. It does not parse PDFs,
load credentials, call a model or write outputs. Without `--sources` it checks
the public reference bundle only, which also runs in CI. PDFs and credentials
remain outside Git; the committed bindings make their exact identity checkable.

To use the same roster with existing parser/cohort commands:

```sh
python -m examples.benchmarks.development --sources /path/to/papers \
  --output results/dev-inputs
```

This creates `papers.json` (explicit article → SI → reference paths) and
`cohort.json` (the existing extraction-cohort format). Add `--repeat-subset` and
choose a different output directory to export the fixed 10-paper subset.
An existing output directory is refused. No inference is performed.
For extraction-cohort commands, supply the method separately with
`--config benchmarks/development/astra.toml`; the dataset does not select a model.

The [study plan](STUDY.md) declares **90 primary runs + 30 repeatability runs**,
human-reference scoring, paired-paper statistics and the approved **$150 campaign
cap**. [astra.toml](astra.toml) is an ordinary extraction configuration for the
frontier baseline. The checker/exporter is not a paid campaign scheduler;
the budget admission and scoring prerequisites in the plan must be completed
before starting the larger study. Native per-paper cost limits do not enforce
the campaign-wide cap.
