# Scorer validation audit — 28 October 2026

## Follow-up: fixes in matcher v8

The v7 audit below found four false rejections. Matcher `rich-study-hungarian-v8`,
fact profile `core-scientific-facts-v6`, addresses them as follows:

| Finding | Change | Check |
| --- | --- | --- |
| Equivalent ranges lost credit | Convert and compare both endpoints | `1–2 h` matches `60–120 min`; a changed endpoint does not |
| Equivalent uncertainty lost credit | Convert the centre and scale the spread | `20 ± 1 %` matches `0.20 ± 0.01`; offset temperatures shift only the centre; nonlinear conversions are not treated as symmetric errors |
| LaTeX formula notation lost credit | Normalize numeric subscripts in simple formulas | `CH_{3}NH_{3}PbI_{3}` matches `CH3NH3PbI3`; different stoichiometry and chemical case remain distinct |
| A cohort-label clarification lost 11 facts | Accept explicit, reference-specific checked equivalences | All 11 population facts match with the checked rules; without them the scorer still refuses to guess |

The label rules are data, not paper-specific code. The optional file
`examples/scoring/reviewed-descriptors-v2.json` records the two names, the reference
hash and the rationale. A direct comparison of the archived snapshot confirmed that
each affected population record was identical to the reference except for its label.
The rules do not change any scientific values or merge reference cohorts. The scorer
stores them in its report, and the ablation planner freezes the same file for both arms.

All 14 equivalence probes now pass. The real-paper controls still pass for all
88 eligible paper/control combinations. In the earlier snapshots, the Chemistry of
Materials population count changes from **0/11 to 11/11** with the checked rules.
Its missing stack fact still loses credit, as do the missing population and changed
stability facts in the other papers. Mean group-balanced F1 across those reviewed
snapshots changes from 0.9889118796 to 0.9955785462 with the rules. This is a correction
of a scoring penalty on reviewed data, **not an improvement in model extraction**.

Validation: **763 tests passed, 63 skipped**, with no expected failures. The separate
release suite passed **124 tests** across both frozen releases. Ruff, type checking
and the strict documentation build also passed. Neither reference JSONs nor
production review data were changed. No model calls were made.

To include the checked label rules in a reproducible audit, add:

```bash
--descriptor-equivalences examples/scoring/reviewed-descriptors-v2.json
```

The audit retains the unadjusted settings alongside the adjusted result. These tests
support the implemented arithmetic and identity safeguards; they do not establish
the scorer's accuracy on arbitrary chemical names or ambiguous device descriptions.
The remaining publication check is an expert-labelled sample of accepted and rejected
matches from genuine model outputs, followed by ranking sensitivity across models.

## Initial verdict: matcher v7

**The sections below preserve the initial audit, before the fixes above.** Counts,
expected failures and unresolved findings in these sections refer to v7, not v8.

The deterministic scorer passes useful arithmetic and identity controls on the
30-paper v2 reference. It also rejects some scientifically equivalent records.
**Do not use the overall score alone to support model-quality rankings yet.**
This audit identifies concrete work before publication; it is not independent
expert calibration or an estimate of the scorer's population error rate.

The tested implementation is `rich-study-hungarian-v7`, fact profile
`core-scientific-facts-v5`. No model calls were made and no reference or production
scoring rule was changed. Test expectations were not relaxed to obtain passing scores.

## 1. Controlled changes to real reference records

Each test starts from a copy of a finalized reference. The reference itself is never
modified. Expected behavior follows the controlled change, not a saved scorer output.

| Control | Papers tested | Result |
| --- | ---: | --- |
| Convert eligible quantities using independently specified factors and offsets | 30 | All passed; 2,159 quantity representations changed without loss of credit |
| Delete the first performance metric, preserving all remaining records | 29 | All passed; only the removed metric lost credit |
| Corrupt scalar outcomes while retaining unique device/protocol identity | 29 | All passed; 359 affected observation identities remained paired correctly; exactly 1,383 changed quantities lost credit, without removing credit from other groups |

The thirtieth paper has no performance observations and is therefore not eligible
for the last two controls. These are 88 paper-level control cases, **not** thousands
of independent validation samples. The deletion control checks one metric per
eligible paper, not every possible deletion. Identity checks cover unique explicit
device/type/scan combinations; they do not establish correctness for every ambiguous
or unlinked specimen.

## 2. A real false rejection: descriptive cohort labels

For `10.1021/acs.chemmater.8b01521`, the older reviewed snapshot and the v2 reference
contain the same two population cohorts and the same 11 population facts. The newer
labels add an explanatory clause: “photovoltaic parameter comparison, not a pooled
mean”. Both versions already use statistic type `other` and no sample size.

The scorer requires the complete normalized cohort label to match exactly. It
therefore awards **0 of 11 population matches**. Changing the similarity threshold
does not help: the label gate rejects the pair before lexical similarity is considered.
An isolated regression reproduces the problem by changing only those labels.

This is not a missing-value error and should not be described as extraction recall
of zero. The cohort identity rule needs a general solution that distinguishes
identifying treatment information from explanatory text while retaining the existing
control/treatment-swap safeguards. Do not fix this by stripping this particular phrase,
by weakening all identity checks, or by trusting matching record IDs in arbitrary runs.

## 3. Explicit equivalence probes

Fourteen synthetic cases specify the expected answer independently of the comparator.
Eleven agree. Three equivalent pairs are currently rejected:

| Reference expression | Equivalent expression | Current result |
| --- | --- | --- |
| 1–2 hours | 60–120 minutes | Different |
| 20 ± 1 percent | 0.20 ± 0.01, dimensionless | Different |
| `CH_{3}NH_{3}PbI_{3}` | `CH3NH3PbI3` | Different |

Positive controls include percent/fraction, hours/seconds, Celsius/Kelvin,
nanometres/metres and Unicode subscripts. Negative controls retain distinctions
between wrong values, wrong unit scales, missing units, a bound versus a point,
an uncertain estimate versus an exact point, and `CoO` versus `COO`.

These unsupported equivalences are documented conservative behavior, but that
does not make their penalties scientifically appropriate. The cases are intentionally
selected stress tests, not a random sample: **11/14 is not an estimated scorer accuracy**.
The test suite records these three gaps and the cohort-label case as strict expected
failures. They remain unresolved; they are not counted as successful validation.

## 4. What the score actually compares

A separate inventory using `scientific_facts()` and `is_plain_number()` found:

| Group | Total scored facts | Reported quantities eligible for the plain-number path | Other reported quantities | Other fields |
| --- | ---: | ---: | ---: | ---: |
| Performance | 1,947 | 1,553 | 394 | 0 |
| Population | 560 | 120 | 391 | 49 |
| Stability | 1,152 | 650 | 502 | 0 |
| Composition | 3,430 | 302 | 277 | 2,851 |
| Stack | 2,200 | 169 | 121 | 1,910 |
| Processing | 5,209 | 2,088 | 792 | 2,329 |

“Other reported quantities” includes text, ranges, uncertainty expressions and
representations that fail the scalar-number check. This audit did not assign a
scientific error label to each such quantity. Plain-number eligibility also does
not guarantee that an unfamiliar unit can be converted. Other fields include
material strings, operations and exact integer sample counts.

The performance group is broader than four photovoltaic numbers: 1,186 of its
1,947 facts use canonical PCE, Voc, Jsc or FF property names. Remaining facts include
other outcomes and measurement conditions. A group-balanced score is consequently
not just an average of the accuracy of those four metrics.

## 5. Sensitivity on archived reviewed snapshots

Two locally available feedback exports supplied 30 development snapshots each.
Snapshot hashes were verified using the feedback exporter's serialization contract.
They are already reviewed data, **not untouched model predictions**.

For each snapshot, the audit compared the v2 reference at five fixed settings:
the default; record similarity thresholds 0.20 and 0.50 instead of 0.35; and relative
numeric tolerances 0 and 1e-4 instead of 1e-6. Absolute tolerance remained zero.

- The later snapshots score 1.0 for all 30 papers. Twenty-six studies are identical
  to the v2 reference; four differ in content outside the scored scientific facts.
  This is consistency evidence, not independent model validation.
- In the earlier snapshots, 26 studies are identical to v2. Three have lower scores;
  a fourth differs without affecting scored facts. The mean group-balanced F1 is
  0.9889118796 at all five settings. All alignments report `ready`.
- The earlier snapshots expose missing population facts in two papers and the
  cohort-label rejection above. The latter is **not flagged as ambiguous**.

Unchanged results across this grid do not prove a correct metric: the label gate
fails identically at every threshold. Nor can these mostly overlapping reviewed
snapshots establish stable rankings among different extraction models.

Unreviewed extraction files located in the workspace were cloud-only placeholders
at audit time. They were not silently replaced by reviewed references and called
model outputs. Genuine cross-model ranking sensitivity remains untested.

## Reproduce the checks

From the checkout, without API keys:

```bash
PYTHONPATH=.:src python -m examples.scoring.validate \
  --release data/study_extraction/ground_truth/v2/release.json \
  --output /absolute/path/to/new/scorer-audit.json
```

Optionally add `--feedback-zip /absolute/path/to/private/feedback.zip` to run snapshot
sensitivity. `--no-controls` skips repeating the expensive reference perturbations
when inspecting another archive. Output files are never overwritten. The JSON keeps
input hashes, scorer settings, code-file hashes, dependency versions, control results,
equivalence discrepancies and per-paper scores. It excludes reviewer identities and
private review text. Missing snapshots remain listed; they are not dropped from the
reported reference roster.

```bash
PYTHONPATH=.:src python -m pytest \
  tests/study_extraction/test_scorer_validation_audit.py -q -rx
```

The four expected failures are deliberate unresolved findings. Remove an expected-failure
marker only when a general fix passes its positive and negative controls. Changes to
the scorer require rescoring every compared system, not editing reference facts.
Adding `--runxfail` exposes the assertions directly: this audit produced **19 passed,
4 failed**, including the real-paper `0 == 11` population-credit failure. The normal
full suite reported **721 passed, 63 skipped, 4 expected failures**. A green CI run
with these markers is not evidence that the scientific gaps have been resolved.

## Next steps supported by these results

1. Resolve cohort-identity matching without losing control/treatment distinctions.
2. Add bounded, validated comparison of range/uncertainty quantities and chemical
   notation. Preserve qualification, units, chemical case and stoichiometry; do not
   replace these checks with blanket fuzzy string matching.
3. Repeat this audit and score genuine saved predictions. Inspect a prespecified
   random sample of accepted and rejected decisions with an expert, supplemented
   by the failure cases above.
4. Only then use scorer results to choose among cheaper pipelines; evaluate the
   locked choice on untouched papers before making generalization claims.
