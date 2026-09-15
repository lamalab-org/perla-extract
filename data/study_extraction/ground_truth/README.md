# Study-extraction ground truth

This directory defines the layout for frozen, human-adjudicated benchmark releases.
No real-paper benchmark items are currently tracked here. A cohort manifest or model
seed is not an adjudicated reference. The executable synthetic example is under
`examples/scoring/` and must not be included in scientific benchmark aggregates.

Mutable review state and original uploads remain private. Before publishing a data
PR, inspect every exported file: review events can include reviewer identities and
comments. The exporter does not anonymize them. Preserve unmodified originals and
create a separately versioned, hash-consistent public derivative when needed.
See [release requirements](../../../docs/methods/benchmark.md#release-and-reproducibility).

Each release version has this layout:

```text
v1/<split>/<paper_id>/
├── ground_truth.json
├── seed_extraction.json
├── review_events.json
└── manifest.json
```

Do not edit these files by hand. Finish adjudication in the review workbench, then run
`review_workbench/export_ground_truth.py`. The exporter revalidates the rich schema,
source citations, current record decisions, and final adjudication before publishing the
directory. It never overwrites a different existing item.

An extractor output or imported review seed is not ground truth and must not be added
here directly, even when every quotation passes deterministic validation. Keep such
outputs in the review system until the source census, record decisions, completeness
check, and administrator adjudication are complete.

`ground_truth.json` is the sole curated truth. Generate reduced or tabular forms with
deterministic adapters rather than maintaining parallel labels.
