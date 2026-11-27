# Historical research data

This directory preserves the human annotations, ground truth, and model outputs used
in earlier PERLA Extract studies. They are useful as provenance and as inputs to future
evaluation work, but they are not part of the production extraction package or CLI.

The files follow the historical reduced PERLA schema. Current extraction and scoring
use the [study model](../../../docs/concepts/study-model.md), with direct NOMAD export.

`ground_truth/reviewed_manifest.json` identifies the completed historical reviews,
their inclusion or non-research exclusion, record counts, content hashes, and the hash
of the hosted review state from which they were exported. It must not be interpreted as
current-schema adjudication. Active human references belong under
`data/study_extraction/ground_truth/v2/` and pass the evidence-backed review gates.
