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
| Subject identity | `scoring_identity.py` | No scored outcomes or completeness-based tie-breaking |
| Context | `scoring_context.py` | Named requirements; preserve missingness, units and multiplicity |
| Fact inventory | `scoring_facts.py` | Project each input without consulting the other; preserve source paths |
| Display categories | `scoring_fields.py` | Categories never select candidate matches |
| Assignment/report invariants | `evaluation.py` | One-to-one, complete inventories, shared denominators |
| Explanations | `scoring_explanation.py` | Consume actual decisions; never run a second matcher |
| Reference coverage | `scoring_coverage.py`, review/export | Source review determines completeness before model comparison |

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

Evaluation format **11**, matcher **v14**, fact profile **v11**, explanation format
**4**, and study schema **7** are current. Ground-truth format **4** is unchanged.
Archived schema-6 inputs load through the additive schema with unknown defaults;
no treatment/protocol is inferred and no archived file is overwritten. Existing
hash-scoped reviewed descriptor rules were rebound to the parsed schema-7 content
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

Reference coverage defaults to unreviewed, including for archived exports. A perfect
self-score does not prove completeness. `reference_agreement` is diagnostic;
`reviewed_scope` requires explicit coverage/context review. Comparisons preserve
failed attempts and diagnostics but withhold a verified quality conclusion for
unreviewed scope. Read the [evaluation contract](../workflows/evaluation.md) for
field definitions and interpretation limits.

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
