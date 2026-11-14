# Change and explain scientific scoring

A scientific value earns primary credit only when its owner is paired, its property
and value agree, its recorded context agrees, and its attribution is unambiguous.
Each fact can be used only once. The three score views make independent assignments:
`strict` requires value and context, `value` ignores owner, context and ambiguity
while retaining scientific group, subfield and property, and
`attribution` ignores the value. None of these views repairs the reference.

## Find the rule

| Work | Location | Contract |
| --- | --- | --- |
| Raw scientific record | `models.py`, `ReportedValue` | Preserve raw text, parsed number, explicit unit and evidence |
| Interpret a claim | `scoring_claims.py`, `normalize_claim` | Return scalar, range, uncertainty or literal; preserve unsupported claims literally |
| Convert explicit units | `units.py` | Handle dimensions, scale and offset without inferring a missing unit |
| Normalize names | `scoring_vocabulary.py` | Use checked material aliases and redundant material qualifiers; preserve unknown chemistry and properties |
| Interpret measurement protocol | `scoring_protocol.py` | One checked vocabulary shared by record identity, fact context and reporting |
| Compare context | `scoring_context.py` | Preserve units, missingness, multiplicity and numeric tolerance |
| Classify fact subfields | `scoring_fields.py` | Assign every fact exactly once; never change parent-group weights |
| Establish record identity | `scoring_identity.py` | Declare identity/protocol fields by type; exclude measured outcomes and metric completeness |
| Assign records | `evaluation.py`, `_record_pairs` | Apply the frozen threshold, parent/protocol bonus and assignment ambiguity checks |
| Align fabrication steps | `scoring_process.py` | Normalize explicit operation/method/target forms; retain only forced pairs across optimal ordered alignments |
| Select and count facts | `scoring_facts.py`, `scientific_facts` | Keep owner/context requirements; merge redundant linked representations with every source path retained |
| Compare facts | `scoring_facts.py`, `compare_facts` | Produce the value, context and ambiguity decision used by every score view |
| Explain an existing decision | `scoring_explanation.py` | Consume those decisions and selected assignments; never run a second matcher |

The normalized claim retains its literal fallback. For example, a scalar with an
unrecognized unit still needs equal raw text and the original unit spelling;
a unit absent from the complete claim never becomes dimensionless. A recognized
unit stated in a plain raw scalar can supply a missing convenience field; a
contradictory field cannot be replaced. Protocol identity and scientific equality use the same numeric tolerances.
Only explicitly shared protocol fields distinguish candidate records; a missing
condition cannot prove that two otherwise identical observations are different.

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
The score records the current matcher and fact-profile versions. See
[the synthetic example](../workflows/scoring-example.md) for environment setup.

Repeat `--explain-truth` or `--explain-prediction` for up to 25 scored field paths.
`--explain-view value` asks about value-only credit; `--explain-view attribution`
asks about context credit. Select canonical paths from `matches` or `unmatched_*_paths`, or any original
source path listed in `core_facts.fact_sources`. The explanation returns both the
requested path and its canonical fact with all source paths; unscored fields such as `/paper/title` are rejected explicitly.
`--explanation-candidates` defaults to 5 and has a maximum of 20 per selected fact.
The artifact marks omitted candidates and truncates long displayed values; the paths
and input hashes identify the complete source data.

A reason of `owner_unmatched` means strict/attribution credit was blocked by the
owner pairing. The value view can still match that fact elsewhere in the same
scientific group. `property_mismatch` lists nearby scored properties within the paired owner;
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

1. Run the example above and explain the missed PCE. Locate its `subfield` and the
   context difference, then find the rule in the ownership table.
2. Inspect the scoped `η` → PCE rule in `scoring_protocol.py`. Its JV positive case
   and EQE counterexample live in `test_scoring_protocol.py`. New aliases belong
   here or in the vocabulary, with an independently specified near-miss test.
3. Trace `photovoltaic_metrics` through `core_facts.subfields`, a match's `subfield`,
   and `figure-data/subfield_scores.csv`. Counts must agree; reporting must not
   reinterpret the extraction.

```bash
PYTHONPATH=.:src python -m pytest -q \
  tests/study_extraction/test_scoring_protocol.py \
  tests/study_extraction/test_scoring_materials.py \
  tests/study_extraction/test_scoring_structure.py \
  tests/study_extraction/test_scoring_explanation.py
```

## Current policy and acceptance checks

Report format **10**, matcher **v13**, fact profile **v10**, explanation format **3**
are the only current contracts. Regenerate older reports from unchanged inputs.
Frozen reference manifests keep their original metadata and data format.

The numeric default remains 1% relative and zero absolute tolerance after unit
conversion. Counts remain exact. Raw/parsed-number consistency and duplicate
merging retain tighter roundoff checks. Changing a tolerance creates a different
comparison policy; reports with different policies cannot be aggregated.

This revision makes explicit protocols constrain outcomes, scopes `η` to photovoltaic
measurements, separates recipe claims, merges redundant whole-material identities,
and normalizes checked annealing conditions. A missing sample size loses its own
point; conflicting reported counts still block the cohort's outcomes. An aligned
annealing duration does not depend on an optional ingredient list; concentration
and ratio claims still do. See the [field contract](../workflows/evaluation.md#subfields-and-their-keys)
for every reporting category and its source fields.

`core_facts.fact_sources` retains all aliases after merging. Selected family matches
also expose `family_features`: shared, reference-only and prediction-only features
from the actual matcher. Similarity is candidate evidence, not scientific credit.

Run `examples.scoring.validate` and the opt-in `test_reference_release.py` checks
against all 30 references before publishing a scoring-policy change. The controls
change outcomes without modifying protocol descriptors. Known ambiguous observations
stay in denominators; outcomes and annotation completeness cannot resolve them.
Do not change a reference or an expected count simply to make the tests pass.

## Remaining scientific validation

Unknown property names, process descriptions and roles remain literal. New concept
mappings need positive and negative examples across papers, independent source
review, and a frozen policy shared by all experiment arms. The thermal-instability
paper (DOI `10.1002/aenm.201500477`, page 6) also supplies important negative controls:
batch results must not become individual-device measurements, and degradation
before device completion must not become a completed-device stability test.

Use `examples.scoring.sample_review` to prepare blinded credit/no-credit samples
from both prediction and reference facts in sealed runs, including reference misses. Independent reviewers must judge property, value, owner, protocol
and source support; include false matches as well as missed matches. The tool
provides blank judgments and a separate analyst key, not expert validation. Sampling
one paper does not establish corpus accuracy or reference completeness. Keep papers
used to develop scoring rules out of held-out claims. Transformation tests establish
software behavior; scientific calibration remains a separate human review task.
