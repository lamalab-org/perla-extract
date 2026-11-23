# Compare extraction models

**30 papers, 57 PDFs, existing human references.** [papers.json](papers.json)
binds each article and SI to the [reference release](../../data/study_extraction/ground_truth/v2/release.json).
The fixed 10-paper subset measures repeatability.

## Run

Set `sources` in [study.toml](study.toml) to your original PDF folder and configure
`OPENAI_API_KEY` and Langfuse. Filenames must match `papers.json`.

```sh
perla-experiment benchmarks/development/study.toml --check
# After PR review; this makes paid requests:
perla-experiment benchmarks/development/study.toml
# Continue interrupted work:
perla-experiment benchmarks/development/study.toml --resume
```

`--check` verifies inputs without parsing, writing or calling a model. Execution
preflights the full roster, then freezes evidence, methods, code and scoring.
Resume reuses completed work; failed or uncertain attempts are never resent.

## Settings

| Stage | Use |
| --- | --- |
| Direct extraction | Baseline: Astra/high, Terra/medium and Luna/medium |
| Refinement | Follow-up: test one Astra review of the cheaper draft |
| Enrichment | Optional composition/processing interpretations for export; does not rewrite `extraction.json` |

The [protocol](STUDY.md) specifies **90 primary + 30 repeatability runs**.
The **$150 admission cap** retains $11.38294587 in prior cost and $17.45 in
unknown-cost holds. Unknown costs keep their reservation; a response can exceed
its estimated hold.

## Results

Open `summary.md` first. `figure-data/important_fields.csv` contains field scores;
`scoring_diagnostics.csv` explains value/context failures. Paired reports under
`comparisons/` contain intervals. Campaign accounting counts shared Astra runs once.

[Saved-pilot counts](pilot-rescore.csv) cover 12 outputs from four papers.
The scorer revision recovers one certified PCE for Luna (65 → 66 of 67 PV reference
fields) and three Terra spectrum matches. Attribution remains weak; this pilot
has no full Astra arm. References are development data with coverage limitations:
these scores measure annotation agreement, not validated absolute accuracy.
