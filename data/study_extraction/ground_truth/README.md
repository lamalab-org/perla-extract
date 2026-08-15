# Study-extraction ground truth

This directory contains frozen, human-adjudicated benchmark items. Mutable review
state, PDFs, and parser documents stay outside Git; only the compact scientific result,
its model seed, audit trail, and provenance manifest enter data PRs.

Each format version has this layout:

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

`ground_truth.json` is the sole curated truth. Generate reduced or tabular forms with
deterministic adapters rather than maintaining parallel labels.
