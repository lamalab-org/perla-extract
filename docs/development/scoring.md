# Change and explain scientific scoring

A scientific value earns primary credit only when its owner is paired, its property
and value agree, its recorded context agrees, and its attribution is unambiguous.
Each fact can be used only once. The three score views make independent assignments:
`strict` requires value and context, `value` ignores context and ambiguity, and
`attribution` ignores the value. None of these views repairs the reference.

## Find the rule

| Work | Location | Contract |
| --- | --- | --- |
| Raw scientific record | `models.py`, `ReportedValue` | Preserve raw text, parsed number, explicit unit and evidence |
| Interpret a claim | `scoring_claims.py`, `normalize_claim` | Return scalar, range, uncertainty or literal; preserve unsupported claims literally |
| Convert explicit units | `units.py` | Handle dimensions, scale and offset without inferring a missing unit |
| Pair records | `evaluation.py`, `_record_features` / `_record_pairs` | Consume normalized claim features; retain the frozen threshold, parent/protocol bonus and assignment algorithm |
| Select facts and requirements | `scoring_facts.py`, `scientific_facts` | Keep owner references and named context components; preserve order and duplicate entries where meaningful |
| Compare facts | `scoring_facts.py`, `compare_facts` | Produce the value, context and ambiguity decision used by every score view |
| Explain an existing decision | `scoring_explanation.py` | Consume those decisions and selected assignments; never run a second matcher |

The normalized claim retains its literal fallback. For example, a scalar with an
unrecognized unit still needs equal raw text and the original unit spelling;
a missing unit never becomes dimensionless. Record features use 12-significant-digit
canonical numbers, while scientific equality uses the configured numeric tolerances.
Feature generation and equality share interpretation, not their acceptance criteria.

`FactContext` components carry a name, value and source path. Their ordered values
retain the original comparison contract; names and paths are diagnostic metadata.
`UnorderedContext` preserves multiplicity, so two identical entries cannot be replaced
by one. `RecordReference` distinguishes a paired owner from a side-local unmatched
reference. A context is not a bag of arbitrary labels: adding or removing a requirement
changes scientific credit and needs explicit counterexamples.

## Explain one field without model calls

The existing synthetic example creates source text, review corrections, predictions,
and scores locally. It requires the scoring example dependencies, not model credentials.
Use fresh output paths each time:

```bash
PYTHONPATH=.:src python -m examples.scoring.run --output /tmp/perla-onboarding
PYTHONPATH=.:src python -m perla_extract.study_extraction.evaluation_cli \
  --truth /tmp/perla-onboarding/truth/dev/10.0000--synthetic \
  --prediction /tmp/perla-onboarding/predictions/seed \
  --output /tmp/perla-onboarding/seed-explained-score.json \
  --explain-truth /performance_observations/0/metrics/0 \
  --explanation-output /tmp/perla-onboarding/seed-why.json
```

The seed PCE differs from the corrected reference. `seed-why.json` names the exact
compared fields, reports `value_mismatch`, and shows both normalized quantities.
The ordinary score retains its existing format. See
[the synthetic example](../workflows/scoring-example.md) for environment setup.

Repeat `--explain-truth` or `--explain-prediction` for up to 25 scored field paths.
`--explain-view value` asks about value-only credit; `--explain-view attribution`
asks about context credit. Select paths from a score's `matches` or
`unmatched_*_paths`; unscored fields such as `/paper/title` are rejected explicitly.
`--explanation-candidates` defaults to 5 and has a maximum of 20 per selected fact.
The artifact marks omitted candidates and truncates long displayed values; the paths
and input hashes identify the complete source data.

A reason of `owner_unmatched` means the fact never reached comparison with a paired
owner. `property_mismatch` lists nearby scored properties within the paired owner;
`no_candidate` means that owner has no opposite scored facts. Candidate explanations
distinguish value differences, named context differences and ambiguity. An eligible
candidate can still lose to `one_to_one_competition`; the explanation names the exact
other field selected for that candidate. Candidate display ordering is independent
of scoring and cannot change an assignment.

The Python API returns both artifacts from one evaluation:

```python
from perla_extract.study_extraction.evaluation import evaluate_study_with_explanations
from perla_extract.study_extraction.scoring_explanation import FactSelection

report, explanation = evaluate_study_with_explanations(
    truth,
    prediction,
    facts=[FactSelection(
        side="truth", path="/performance_observations/0/metrics/0", view="strict"
    )],
    max_candidates=5,
)
```

The explanation records the input hashes, scorer versions and effective configuration
from that report. Checked descriptor equivalences still operate on scorer-local copies;
the hashes identify the original inputs.

## A first contribution exercise

Timebox a new maintainer's first pass to half a day:

1. Explain why explicit `20 %` and `0.20 dimensionless` match, then why the same
   value attached to the wrong scan loses strict credit but keeps value-only credit.
   Use the public explanation API and identify the source context field.
2. In a separate experimental change, support literal LaTeX `\pm` as another
   spelling of symmetric uncertainty. It is deliberately unsupported by the current
   scorer. Make the interpretation change in the claim parser; both equality and
   record features should consume it through `normalize_claim`.
3. Write independently expected positive cases in both directions, including
   `20 \pm 1 °C` versus `293.15 ± 1 K`. Add negative cases for an inconsistent
   parsed number, a changed spread, and attached `SD`/`SEM` qualifiers. Do not
   reinterpret a range, inequality or unknown qualifier as an uncertainty.
4. Use two distinct cohorts and reordered records to demonstrate that representation
   changes preserve attribution, while swapping their outcomes still loses credit.
   Assert the public fact paths and counts, not private lexical feature sets.
5. Run the focused checks below. Explain any changed score before changing an
   expectation. An intentional new equivalence is a scoring-policy change; review
   the matcher/fact-profile version policy before proposing it for a benchmark.

```bash
PYTHONPATH=.:src python -m pytest -q \
  tests/study_extraction/test_scoring_contracts.py \
  tests/study_extraction/test_scoring_equivalence.py \
  tests/study_extraction/test_scoring_explanation.py \
  tests/study_extraction/test_scoring_refactor_invariance.py
```

The frozen-reference test checks known equivalent-unit, missing-metric and changed-
outcome controls on representative v2/dev papers, then verifies that requesting an
explanation leaves the entire report unchanged. Expectations are scientific edits,
not hashes that change with a dependency's lexical normalization.

The refactor was also audited against the original `2b1d81c` scorer with 118 full-report
comparisons across all 30 frozen v2/dev papers. Every report was identical within
each of two tested environments (Pydantic 2.12.5/Pint 0.25.2 and Pydantic 2.14.3/Pint
0.26.2). Compare old and new code in the same environment for future refactor audits;
raw full-report hashes can differ across dependency versions. This parity check
does not prove that the old scorer was scientifically correct: the hand-checked
positive/negative cases remain the scientific oracle.
