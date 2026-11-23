# Reusable development set

**30 papers, 57 original PDFs, existing human-adjudicated references.**
[papers.json](papers.json) fixes each article/SI role, SHA256, DOI and a
10-paper repeatability subset. It pins the already committed
[reference release](../../data/study_extraction/ground_truth/v2/release.json);
answers are not copied into another ground-truth format. These model-assisted,
human-finalized references have informed development; they are not a held-out test.
Reference JSON uses the published canonical content-hash convention, so harmless
formatting or line-ending changes do not alter its identity. PDF hashes bind exact bytes.

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

The [study plan](STUDY.md) declares **90 primary runs + 30 repeatability runs**.
[study.toml](study.toml) runs Astra, Terra and Luna against the same human references.
It loads the ordinary [astra.toml](astra.toml) method, then overrides only the
cheaper deployments. Edit `sources` in `study.toml` to your original PDF folder.

```sh
perla-experiment benchmarks/development/study.toml --check
# After reviewing the method and approving paid execution:
perla-experiment benchmarks/development/study.toml
# If interrupted, continue the same frozen experiment:
perla-experiment benchmarks/development/study.toml --resume
```

`--check` does not parse, write or call a provider. Execution preflights all 30
papers/arms before the first paid call and freezes the prepared evidence, cache,
code, runtime, references and order. Primary runs finish before second runs on
the fixed 10-paper subset. Every attempt has its own response cache.

The **$150 admission cap** includes $11.38294587 of prior recorded cost and $17.45
of prior unknown-cost reservations. The per-arm reservations are conservative
admission estimates, not price guarantees; an unexpectedly expensive response
can exceed its hold. Unknown costs keep their hold; failed attempts keep their
spend. Resume never re-dispatches a reserved slot, including one killed before
writing a report. It can continue other planned slots within the cap.

Open `summary.md` first. `comparisons/terra-vs-astra/primary/` and the Luna
equivalent contain paired effects, paper-bootstrap intervals and figure tables.
Their `repeatability/` counterparts contain only the fixed subset and both runs.
The root `figure-data/` uses actual arm names and includes each unique attempt
once; `important_fields.csv` includes PCE, FF, Voc and Jsc, while
`field_strata.csv` distinguishes measurement methods and statistical contexts;
`scoring_diagnostics.csv` names each field and distinguishes parsed quantities
from literal and nonquantity assertions.
`spending.json` retains reservations and sealed-report identities. `accounting.json`
reports campaign totals. Paired reports reuse Astra artifacts; their costs must
not be summed as if Astra had been extracted twice.

These development references have incomplete coverage in some domains. Their
automatic agreement scores support comparisons; they do not establish absolute
precision or held-out accuracy. No new human labels are required for this study.

## Saved-pilot check before the larger study

[pilot-rescore.csv](pilot-rescore.csv) records the same 12 saved outputs (four
papers, three deployments) under matcher v25 and v26, including all denominators,
claim types and evaluation hashes. No new extraction calls were made.

| Existing pilot | PV recovery matches / 67 reference fields, before → after | Attributed PV matches, before → after |
| --- | --- | --- |
| Terra / medium | 67 → 67 | 12 → 12 |
| Terra / low | 67 → 67 | 15 → 15 |
| Luna / medium | 65 → 66 | 13 → 14 |

These are PCE/FF/Voc/Jsc field counts, retaining literal and malformed claims.
Prediction counts differ by arm; use the CSV for precision and recall denominators.
The recovered Luna field is explicitly certified PCE. Terra's explicit AM1.5G
spectrum fields also recover three protocol matches, without earning attribution
credit where other context is missing. No tolerance or reference was changed.
Attribution remains the main unresolved limitation. This pilot contains no full
Astra arm and cannot establish the best deployment or predict the 30-paper result.
