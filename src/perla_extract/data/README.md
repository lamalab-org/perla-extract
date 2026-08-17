# Historical research data

This directory preserves the human annotations, ground truth, and model outputs used
in earlier PERLA Extract studies. They are useful as provenance and as inputs to future
evaluation work, but they are not part of the production extraction package or CLI.

The files follow the historical reduced PERLA schema. New workflow outputs use the
device-centered [study model](../../../docs/concepts/study-model.md); `reduced.json` is
the deterministic compatibility representation.

`ground_truth/reviewed_manifest.json` identifies the completed historical reviews,
their inclusion or non-research exclusion, record counts, content hashes, and the hash
of the hosted review state from which they were exported. It must not be interpreted as
rich-schema adjudication; rich ground truth belongs under
`data/study_extraction/ground_truth/v1/` and passes the newer evidence-backed gates.
