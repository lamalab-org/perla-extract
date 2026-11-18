# Expert-reviewed reference release v1

This release contains **30 finalized references**, frozen from the review app on
23 October 2026. Each study began as a model extraction and was reviewed against
the source paper. Use these as development references, not as an independent
held-out test set: these papers and corrections have informed the pipeline.

## Use it

From a checkout of this repository, with the package installed:

```bash
perla-evaluate \
  --truth data/study_extraction/ground_truth/v1/dev/10.1002--adfm.201500335 \
  --prediction /path/to/your/extraction-run \
  --output evaluation.json \
  --fail-on-scoring-issues
```

Replace the prediction path with a run directory containing `extraction.json`,
preferably also `document.json` and `report.json`. The scorer checks reference
hashes and schema compatibility. Never supply the reference to the extraction
model. Keep failed runs in the comparison roster.

`release.json` lists every paper, reference revision and content hash, together
with the scoring configuration used to check this release. The baseline uses
matcher `rich-study-hungarian-v6` and fact profile `core-scientific-facts-v4`.
Record later scorer versions separately; do not mix their reports silently.

## What is published

Each `dev/<paper>/` directory contains:

- `ground_truth.json`: the finalized study, **byte-identical to its private export**.
- `manifest.json`: a separate public manifest preserving the scientific data
  hash, review revision, schema identity, evidence-document identity, source PDF
  hashes and validation summary.

The public manifest omits account identities, review comments, local filesystem
paths and internal extraction diagnostics. Its `publication` entry links it to
the private export by hash. Its source-manifest hash differs from the private
version because that metadata was reduced; the scientific data hash does not.

Original uploads, review events, historical model seeds, PDFs and parsed evidence
documents remain private. This is a scorer input, not a replacement for the
private audit archive or a bundle to import as new review history. The data retains
supporting source quotations and block identifiers; consult the cited papers for
scientific verification. Publisher PDFs and figure images are not distributed.

## Verification and limits

Before publication, the private audit checked that all 30 exports matched their
saved finalized studies. Replaying 1,144 events, including 129 edit operations and
18 explicit full-study refreshes, reproduced those studies. It checked 3,245
decision fingerprints; these counts are not counts of unique human corrections.

CI checks the public hashes and loads all 30 references through the real scorer.
The dedicated `reference-release` job also verifies that:

1. Each reference scores perfectly against itself, without matching warnings.
2. Renaming generated IDs and reordering arrays preserves the score.
3. Wrong numbers, missing records and duplicate records lose credit.

The negative controls use individual performance observations where present and
population statistics otherwise. No paper is excluded to make the controls pass.
One study (`10.1002--aenm.201500477`) has population statistics but no individual
performance observations in its finalized reference.

These checks establish data preservation and specified scorer behavior. They do
not prove that every scientific judgment is correct or that no source information
was missed. Scientific correctness rests on the expert review. Model-generated
`unresolved_notes` remain unchanged in the data; they are not scored facts or
outstanding record-review decisions.

To repeat the controls without model calls:

```bash
PERLA_RUN_REFERENCE_RELEASE_TESTS=1 python -m pytest -q \
  tests/study_extraction/test_reference_release.py
```

Do not edit this release in place. Incorporate later corrections through the
review workflow and publish a new version, retaining this version for reproducible
comparisons. Do not combine it with the historical reduced-schema labels under
`src/perla_extract/data/ground_truth/` as though they were the same benchmark.
