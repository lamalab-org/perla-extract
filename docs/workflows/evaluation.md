# Evaluate an extraction

`perla-evaluate` compares a rich `extraction.json` with one frozen, adjudicated
`StudyExtraction`. It is deterministic and never calls an LLM. Run-local record IDs
and evidence quotations are excluded from record similarity; evidence validity is a
separate extraction-validation result.

```bash
perla-evaluate \
  --truth data/study_extraction/ground_truth/v1/dev/10.1126--science.adf0194 \
  --prediction results/10.1126--science.adf0194 \
  --output results/10.1126--science.adf0194/evaluation.json
```

When `--truth` is a frozen benchmark directory, the command verifies the schema hash
and canonical `ground_truth.json` content hash before scoring. The resulting report
also records the paper ID, split, truth hash, source-document hashes, and a fallback
source-manifest hash. Passing a bare truth JSON is useful for development but does not
provide those provenance checks.

Prefer a complete extraction run directory for `--prediction`. The command then
recomputes evidence and relationship validation from `extraction.json` and
`document.json` and embeds the result in the score report. It also validates and
retains measured calls, tokens, reported cost, and elapsed time from `report.json`.
A bare prediction JSON is accepted for development, but its report marks validation
and efficiency accounting as unavailable.

## What is scored

The report keeps distinct questions separate:

- inventory precision, recall, and F1 for families, devices, observations,
  populations, and stability tests;
- the exact record pairings selected by a global one-to-one matcher;
- scalar-field agreement on matched records;
- parent-link agreement on matched records, such as whether an observation points to
  the matched device;
- end-to-end atomic `ReportedValue` precision/recall across scored records and
  conditional value agreement for matched quantities, including compatible unit
  conversion; and
- unmatched truth and prediction record keys for error analysis.

The matcher uses a versioned, transparent lexical/content similarity and a Hungarian
assignment. It does not greedily match records in file order. The threshold and
numeric tolerances are stored in every report. Do not tune them on the held-out test
split.

Rates with a zero denominator are `null`, not a vacuous perfect score. Always retain
the predicted, truth, and matched counts when aggregating reports.

## Reviewer uncertainty

A record marked `uncertain` at final adjudication is not a positive or negative label.
The format-3 ground-truth manifest stores those record keys as an abstention mask and
binds the truth revision to the evidence-document version used during review.
Certain truth records are matched first; a remaining prediction that matches an
uncertain record is excluded from both precision and recall. The report lists every
masked prediction so abstention cannot silently improve a score.

## Dataset reporting

After inspecting the per-paper pairings, aggregate their immutable reports without
rerunning matching:

```bash
perla-evaluate-dataset \
  --report results/paper-a/evaluation.json \
  --report results/paper-b/evaluation.json \
  --output results/model-x.dataset-evaluation.json
```

The aggregator refuses reports with different schema hashes, matcher versions, or
threshold/tolerance configurations. It also refuses to mix provenance-verified and
development reports, benchmark splits, duplicate paper IDs, or duplicate source
documents/manifests. It reports micro counts for records, fields, relationships, and
atomic values; macro paper-level rates; and deterministic 95% paper-bootstrap
intervals. It also totals how many predictions carried evidence validation, how many
were verified, how many validation issues remained, and all available run-efficiency
counts. Undefined paper-level rates are excluded with their contributing paper count
reported explicitly.

Keep calibration, development, and test manifests separate. Papers used to change
parsing, prompts, schemas, matching, thresholds, or model selection are not held out.
