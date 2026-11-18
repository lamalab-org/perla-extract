# Change and explain scientific scoring

There is one canonical fact inventory and two independently assigned views:

- `core_facts`: correct values with compatible scientific context and unambiguous attribution.
- `core_facts.recovery`: compatible concept/value recovered anywhere in the paper, including across individual/population containers.

A recovery match cannot establish specimen identity. One prediction cannot satisfy
several reference facts in either view. Wrong values remain both a missed reference
fact and an extra predicted fact. No LLM judges, paper-specific rules or
prediction-dependent exclusions are part of the default scorer.

```text
project each input independently → align subjects without outcomes
→ compare compatible concepts → assign once per view → summarize the decision ledger
```

## Where a rule belongs

| Change | Owner | Requirement |
| --- | --- | --- |
| Concept/name equivalence | `scoring_vocabulary.py` | Checked positive and negative examples; unknowns stay literal |
| Measurement protocol | `scoring_protocol.py` | Identity and outcome context use the same definition |
| Quantity interpretation | `scoring_claims.py`, `units.py` | Preserve units, bounds, uncertainty and raw/parsed contradictions |
| Subject identity | `scoring_identity.py`, `scoring_layers.py` | No scored outcomes or completeness-based tie-breaking |
| Measurement ownership | `scoring_identity.measurement_subject` | Explicit device links identify subjects; observation IDs only group fields |
| Context | `scoring_context.py` | Named requirements; preserve missingness, units and multiplicity |
| Fact inventory | `scoring_facts.py` | Project each input without consulting the other; preserve source paths |
| Display categories | `scoring_fields.py` | Categories never select candidate matches |
| Assignment/report invariants | `evaluation.py` | One-to-one, complete inventories, shared denominators |
| Explanations | `scoring_explanation.py` | Consume actual decisions; never run a second matcher |
| Reference coverage | `scoring_coverage.py`, review/export | Source review determines completeness before model comparison |

To change which fields count, start at `scientific_facts()` in `scoring_facts.py`.
It visits the five record collections in schema order. `_FactProjector.family`,
`.device`, `.performance`, `.population` and `.stability` each select fields from
one record type. Shared helpers attach context and source paths; `finish()` merges
explicitly redundant representations. For example, a new individual performance
field belongs in `.performance`, not in the record matcher or CSV writer. Each
projection reads one input only; it cannot use the other input to fill missing facts.

The concept key separates recipe versus finished material, static measurement
versus aging-test scope, and absolute efficiency versus retention. Reporting uses
four domains: performance, material/device identity, processing and stability.
Qualitative statements, unparsed measurement claims and statistical metadata stay
visible separately from parsed quantitative outcomes.

`FactContext` compares fields by name, not insertion order. Names are unique within
a scope. Differences identify their source paths and distinguish conflicts from
missing requirements. A missing sample count loses its own point; two conflicting
counts block the cohort's outcomes. SD error bars are independent of an outcome's
mean/median/other statistic.

`PopulationStatistic.subjects` and `IndividualDevice.subject_context` store explicit
treatment conditions, treated material and treatment stage. Structured subject
identity permits label paraphrases only when both inputs independently supply
compatible evidence. Otherwise conservative labels remain the fallback. A shared
subject set is one assertion, not permission to multiply it into several matches.
Repeated indistinguishable subjects remain ambiguous, even with different outcomes.
Only the checked treatment-condition names may establish identity. Unknown fields
remain stored/scored but cannot break a tie; a misplaced PCE cannot become identity.

The same explicit sample-code parser supports families and cohorts. Known codes
must agree; extra label prose adds no evidence. A same-code family can align despite
an incomplete stack, but the missing layers remain misses. PV context comparison
distinguishes omitted reference scan/method/subjects from explicit contradictions;
known reference conditions still need a prediction. Champion-status omission does
not veto an already aligned specimen's value. See `test_attribution_missingness.py`
for positive cases and wrong-code, wrong-statistic and conflicting-context controls.

Repeated observations of an independently established device are different from
ambiguous subjects. Individual performance facts use that device as owner and keep
their complete measurement context. The fact matcher handles their multiplicity;
it does not need a unique pairing of observation IDs. Cohorts and stability tests
retain their own identity. See `test_measurement_attribution.py` for the split/merge,
duplicate, missing-link and ambiguous-device contracts.

## Identity, required context and description

| Information | Role in scoring |
| --- | --- |
| Ordered layer materials, explicit treatment inputs, exact arm labels when needed | Establish correspondence without measured outcomes |
| Treatment stage/material, scan, statistic, supplied measurement protocol | Required meaning of an attributed claim |
| A long variant description already established by explicit treatment inputs | Redundant wording; does not veto the outcome |
| Missing distinguishing inputs or ambiguous owners | Unresolved; no attributed credit |
| Explicitly different inputs, materials or protocols | Contradictory; no attributed credit |
| Source evidence and citation validity | Checked separately; recovery cannot establish support |

Device treatment properties and structured conditions share one independent
projection. Unknown conditions remain required context but cannot establish
identity. A label remains a fallback when recognized inputs are absent. Layer
alignment uses ordered materials and passes the same links to composition and
recipe targets. It never fills omitted layers. See the [paper methods](../methods/scientific-scoring.md)
for the exact similarity equation, tolerances, score definitions and limits.

## Run and inspect the existing example

```bash
PYTHONPATH=.:src python -m examples.scoring.run --output /tmp/perla-scoring-example
PYTHONPATH=.:src python -m perla_extract.study_extraction.evaluation_cli \
  --truth /tmp/perla-scoring-example/truth/dev/10.0000--synthetic \
  --prediction /tmp/perla-scoring-example/predictions/wrong_scan \
  --output /tmp/perla-scoring-example/explained.json \
  --explain-truth /performance_observations/0/metrics/0 \
  --explain-view attributed \
  --explanation-output /tmp/perla-scoring-example/why.json
```

The wrong-scan variant contains the correct PCE, so recovery succeeds while
attribution fails. `why.json` identifies `scan_direction`; changing `--explain-view`
to `recovery` explains the other assignment. All inputs are synthetic, and no model
calls or credentials are needed. Use a fresh output path when rerunning.

The explainer accepts up to 25 canonical/source-alias paths and retains original
input hashes. It bounds candidate displays without changing assignments. A missing
owner is distinct from a value mismatch; `one_to_one_competition` names the fact
that consumed an otherwise eligible candidate. `fact_sources` preserves aliases
when explicitly redundant linked material representations merge.

## Current contract and migration

For the local dependency rules, start with `scoring_requirements.py`. It declares
the subject, defining fields and independent fields for each supported rule.
`scoring_protocol.py` alone handles explicit/legacy measurement conditions;
`scoring_diagnostics.py` summarizes the actual decisions without matching again.
The [methods description](../methods/scientific-scoring.md#fact-local-requirements)
includes the rule table, pseudocode and limitations.

Run the automatic controls without credentials or new labeling:

```bash
PYTHONPATH=.:src python -m examples.scoring.validate \
  --release data/study_extraction/ground_truth/v2/release.json \
  --output /tmp/scoring-controls.json
```

Use a new output path. Read `calibration_coverage` for applicable, passed and
inapplicable cases; the tool exits unsuccessfully if any applicable control fails.
Synthetic conditions are explicitly marked and never written back into references.

Evaluation format **16**, matcher **v21**, fact profile **v18**, explanation format
**6**, and study schema **8** are current. Ground-truth format **4** is unchanged.
Archived schema-6/7 inputs load through the additive schema with unknown defaults;
no treatment/protocol is inferred and no archived file is overwritten. Existing
hash-scoped reviewed descriptor rules were rebound to the parsed schema-8 content
without changing their labels or scientific rationale.

Regenerate old reports from saved extractions. There is no legacy scoring engine.
Experiment comparison now uses equal-paper mean differences in attributed **micro
F1**, averaging repetitions within each paper. Outputs name this `fact_f1` and
`mean_paired_fact_f1_delta`; they must not be compared directly with the former
group-macro scores. Pooled counts and domain breakdowns remain necessary because
frequent fact types carry more weight within a paper.

Numeric tolerance remains **1% relative, zero absolute**, after unit conversion.
Counts are exact. Duplicate merging allows conversion roundoff only, independently
of scoring tolerance. Unknown or inconsistent quantities remain literal.
PV units explicitly written in a metric name can fill a missing unit field in the
scoring projection. `unit_source` in fact diagnostics records the source or the
unresolved state. No unit is inferred from another fact, a citation or the paired
reference. Unitless absolute PCE/FF use percentage points, after checking explicit
units in the field, label and complete raw claim. The projection records
`unit_source=percentage_points`; no value-size heuristic is used. See
`test_scoring_percentage_points.py` for symmetric comparison, fraction-scale,
raw/parsed contradiction and complete range/uncertainty controls.
`units.py` accepts unit names and arithmetic after its explicit typography rules.
Comments, braces and secondary quantities in a unit label remain unparsed; Pint
versions must not silently remove punctuation and change their interpretation.
Malformed parser input also remains literal instead of aborting the paper score.

Reference coverage defaults to unreviewed, including for archived exports. A perfect
self-score does not prove completeness. `reference_agreement` is diagnostic;
`reviewed_scope` requires explicit coverage/context review. Comparisons preserve
failed attempts and diagnostics but withhold a verified quality conclusion for
unreviewed scope. Read the [evaluation contract](../workflows/evaluation.md) for
field definitions and interpretation limits.

The same comparison emits `reference_agreement` automatically from verified,
complete pairs. `complete_roster` means all planned pairs contributed;
`conditional_on_complete_pairs` lists omitted repetitions. It is diagnostic, not
certified accuracy. Artifact, reference, scorer or design incompatibility blocks
this contrast too. The CLI succeeds for a complete diagnostic comparison; use
`--require-reviewed-quality` to require the stricter publication gate. No review
of individual predictions is part of the ablation loop.

## Acceptance checks

```bash
PYTHONPATH=.:src python -m pytest -q \
  tests/study_extraction/test_scoring_semantics.py \
  tests/study_extraction/test_scoring_protocol.py \
  tests/study_extraction/test_scoring_structure.py \
  tests/study_extraction/test_scoring_explanation.py
```

The semantic counterexamples cover cross-container recovery, duplicates, wrong
statistical level/treatment/stage, label paraphrases, shared assertions, ambiguous
identity, category independence and reference completeness. Existing tests retain
unit, tolerance, material, process, roster and artifact-integrity controls.

Before releasing a scoring change, run `examples.scoring.reference_audit` and
`examples.scoring.validate` across the frozen 30-reference roster. Retain decision
level deltas when rescoring saved predictions. Separate code effects from reference
adjudication and summary-statistic changes. Never update an expected score solely
to make a test pass.

A new maintainer should be able to explain the wrong-scan example, locate its
context rule, and add a checked alias with a near-miss test without editing the
assignment or report code. Independently reviewed cross-paper samples and a
held-out set remain necessary for scientific calibration. The existing
`examples.scoring.sample_review` creates blinded blank review sheets and a separate
analyst key; generating them does not constitute expert validation.


To sample a revised score against a retained baseline, without changing the run:

```bash
PYTHONPATH=.:src python -m examples.scoring.sample_review \
  --truth path/to/reference --prediction path/to/sealed-run \
  --score path/to/revised-evaluation.json --baseline path/to/original-evaluation.json \
  --view both --per-stratum 5 --seed 17 --output path/to/new-review-packet
```

The review sheet names the score view and leaves decisions blank. The analyst key
contains inclusion probabilities, credit/change strata and both score contracts.
Inventory removals remain in the complete replay ledger; a current-score sample
cannot select a fact no longer in its inventory. Historical JSON is read only by
the audit comparison helper, not supported as a second runtime scorer.

The [saved-extraction validation report](context-calibration-results.md) records
what changed in the 117-output replay, the automatic controls and the remaining
context limitations.
