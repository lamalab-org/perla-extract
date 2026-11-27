# Development, then human evaluation

| Dataset | References | Use |
| --- | --- | --- |
| Development | Strongest feasible model extractions on separate papers | Tune prompts, models, refinement, parsing and scoring |
| Test | Existing human-corrected records for 30 papers / 27 SIs | Evaluate frozen pipelines |

Optimize on model-generated references; reserve human references for the final
evaluation. **Paid execution starts after PR review.**

## 1. Build model-labeled development data

Choose papers disjoint from `test/papers.json` by DOI and source hashes. Commit
the roster before generation. Use the most capable extraction pipeline we can
support, including refinement if useful; `development/astra.toml` is an
uncalibrated starting configuration. Inspect source support, completeness,
truncation and repeatability without consulting the human test annotations.
These checks cannot establish that a teacher is perfectly accurate.

Freeze source-linked teacher outputs, model/settings, prompts, parser/schema,
code/dependencies and usage. Keep the existing extraction format and record
model-generated provenance; do not invent human review or adjudication.
Development references have not yet been generated.

## 2. Optimize on development data

Compare cheaper direct models, reasoning settings and mixed-model refinement
against the frozen development references. This is **teacher agreement**, not
human accuracy. Retain reference errors, generation failures, costs and provenance
for inspection. Do not replace a teacher reference because a cheaper model scores
poorly against it. Any revision needs source evidence and a recorded new version.

Enrichment produces separate database interpretations; it does not rewrite the
scored extraction. Keep it outside extraction-quality comparisons unless its
interpretations receive a separate evaluation.

Freeze candidate pipelines and the scorer before opening final test results.
No test feedback is used for further selection in this study.

## 3. Score the human-corrected test set

Use all 30 papers and 27 SIs in `test/papers.json` (57 PDFs, 1,143 pages). The
current recipe compares Astra, Terra and Luna: 90 first runs, then 30 second runs
on the fixed subset. Final settings come from development; budget these runs
after development costs are known. Keep failures and unfinished slots visible.

The repeatability subset contains 3 short, 3 medium and 4 long papers. Within
combined-PDF-page length thirds, choose by ascending SHA256(`20261122:paper_id`).
This selection measures test repeatability; it is not a development subset.

Preflight every paper, reuse identical prepared evidence across arms and give
every attempt an independent response cache. Freeze sources, references, methods,
code and order; shuffle paper blocks/arms with seed 20261122. Never truncate or
remove a difficult paper after seeing scores.

Freeze the [current scoring contract](../docs/methods/scientific-scoring.md) and
its code/dependency hashes before the paired development comparison.
Use 1% relative / zero absolute numeric tolerance and percentage points for
unitless PCE/FF. Report PCE, FF, Voc, Jsc, identity, processing and stability, with
precision/recall/F1 and raw counts. Separate retrieval from strict attribution;
retain malformed/literal claims in field denominators.

Use equal-paper paired mean differences and 95% paper-bootstrap intervals
(10,000 resamples, seed 20261122). Analyze repeats separately; the independent
unit remains the paper. Any complete-pair estimate names its denominator.
An interval crossing a predeclared quality target is inconclusive.

These human references already informed earlier development. Their current role
is test evaluation, but results are not an untouched held-out estimate. Preserve
the archived release's legacy `dev` metadata and disclose prior use. Existing
coverage limitations also constrain absolute precision claims.

## Budget and artifacts

The $150 total admission cap retains $11.38294587 in prior recorded cost and
$17.45 in unresolved holds. Development and final testing share that budget;
carry every phase's spending/holds forward before starting another output folder.
Do not reset the allowance for each phase.

Use one active generative request. Persist reservations before dispatch and settle
only with complete accounting; unknown costs keep their hold. A response can
exceed its estimate. Stop admissions at the cap without hiding unfinished work.

Keep full Langfuse traces and local sealed artifacts. Publish sanitized field,
failure and cost tables with code/scorer versions. PDFs, prompts, credentials and
private review history stay outside Git. No additional human labeling is required.
