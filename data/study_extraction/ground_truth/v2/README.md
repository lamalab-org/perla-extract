# Source-corrected reference release v2

This release contains 30 model-assisted, expert-reviewed development references.
It incorporates source-checked amendments to four papers. The other 26 scientific
JSON files are byte-identical to [v1](../v1/README.md), which remains unchanged.
These papers have informed the extractor: do not present scores on them as a
held-out test result.

## Score an extraction

```bash
perla-evaluate \
  --truth data/study_extraction/ground_truth/v2/dev/10.1126--science.adf0194 \
  --prediction /path/to/extraction-run \
  --output evaluation.json \
  --fail-on-scoring-issues
```

The prediction directory must contain `extraction.json`; keep `document.json` and
`report.json` alongside it when available. Never pass the reference to the
extraction model. Keep failed extraction runs in the comparison roster. Use one
fixed reference version and scorer configuration for every model in a comparison.

`release.json` identifies all papers, revisions, hashes and scoring settings.
Each paper directory contains the finalized `ground_truth.json` and a sanitized
`manifest.json` accepted by the scorer. These are actual finalized app exports,
not regenerated model outputs or manually edited substitutes.

## What changed

See the [record amendments and their limits](../../reference_amendments/2026-10-23/README.md).
In brief:

- Retain the PEACl Voc of 1.13 V, supported by the printed S11 row.
- Restore the Nano Energy additive-screening and steady-output comparisons;
  stop asserting five devices specifically in the target air-storage arm.
- Retain the Au layer labelled in cross-section without assigning an electrode
  role, thickness or deposition process. Keep untreated and post-treated cohorts separate.
- Restore the Nature Communications screening populations without inventing
  individual cells or numerical distribution summaries.

The obsolete extraction notes for these amendments were replaced through logged
app mutations. The public `notes-corrections.json` records their exact before/after
text. Original annotations, prior decisions and original workbooks remain in the
private history. Earlier decisions were reconciled explicitly; this maintenance
pass was not a new independent expert review of every field in every paper.

## What the checks establish

The private audit replays the saved history, checks before-values and decision
fingerprints, and reproduces each exported JSON. The public tests verify hashes,
schema compatibility and the exact declared changes. Both release versions are
tested with the real scorer: self-comparison and reordered/renamed equivalents
score perfectly; wrong numbers, omitted records and duplicates lose credit.

```bash
PERLA_RUN_REFERENCE_RELEASE_TESTS=1 python -m pytest -q \
  tests/study_extraction/test_reference_release.py \
  tests/study_extraction/test_reference_amendments.py
```

These checks establish preservation and specified scoring behavior, not perfect
scientific completeness. In particular:

- The caption-only control-air experiment has no available control outcome. Its
  conditions remain in the linked inventory, outside the outcome-requiring scoring
  schema. Do not copy the target endpoint or count the inventory as a scored record.
- Some condition-level comparisons have no supported individual-cell link or
  numerical outcome. Their qualitative wording is retained without fabricated values.
- Figure-only Au evidence was inspected visually. Its quoted caption locates the
  image but does not itself prove the Au label. Report input coverage when comparing
  text-only extraction with references reviewed using the full paper.
- Recipe grouping and source ambiguities remain documented. A finalized record
  can faithfully state a limitation; finalization does not make an unsupported
  physical interpretation certain.

The public derivative contains scientific records and source quotations, not
reviewer identities, private feedback, original workbooks, PDFs or credentials.
Retain the private audit archive separately. Never edit either frozen release in
place; publish a new version after later corrections.
