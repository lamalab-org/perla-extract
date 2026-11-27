# Change and explain scientific scoring

One fact inventory supplies two independently assigned views:

- `core_facts`: correct values with compatible scientific context and unambiguous attribution.
- `core_facts.recovery`: compatible concept/value recovered anywhere in the paper.

One prediction cannot satisfy several reference facts in either view. A recovery
match does not establish specimen identity or source support. Wrong and malformed
claims remain in their declared denominators.

```text
project each input independently → align subjects without outcomes
→ compare compatible concepts → assign once per view → summarize the decision ledger
```

The [scientific specification](../methods/scientific-scoring.md) owns matching
rules, numerical tolerances, score definitions and limitations. The
[evaluation guide](../workflows/evaluation.md) explains public commands and outputs.

## Where a rule belongs

| Responsibility | Owner |
| --- | --- |
| Complete quantity interpretation and projection | `quantities.py` |
| Checked quantity names, dimensions and conventions | `quantity_specs.py` |
| Numerical unit conversion and notation | `units.py` |
| Scientific concept/name equivalence | `scoring_vocabulary.py` |
| Outcome-free identity text and subject pairing | `identity_text.py`, `scoring_identity.py`, `scoring_layers.py` |
| Measurement protocol and ownership | `scoring_protocol.py`, `scoring_identity.measurement_subject` |
| Required scientific context | `scoring_context.py`, `scoring_requirements.py` |
| Fact inventory | `scoring_facts.py` |
| Display categories | `scoring_fields.py` |
| Assignment and report invariants | `evaluation.py` |
| Explanations of actual decisions | `scoring_explanation.py`, `scoring_diagnostics.py` |
| Reference completeness declarations | `scoring_coverage.py`, reference review/export |

Start at `scientific_facts()` in `scoring_facts.py` to change which fields count.
`_FactProjector.family`, `.device`, `.performance`, `.population` and `.stability`
select fields from their record types. Shared helpers attach context and original
paths; `finish()` merges explicitly redundant linked representations. Each
projection reads one input only; it cannot borrow facts from the paired reference.

Unit strings such as `W/m2` are machine-readable inputs to the shared converter.
For example, `100 mW/cm2` and `1000 W/m2` represent the same irradiance. Pint
handles compatible units; source strings remain intact. Add a special notation
rule only for a demonstrated parser limitation, with an independent positive and
negative case. Do not add a scoring branch for every spelling.

## Follow a missed fact

Run the [five-case offline example](../workflows/scoring-example.md), then explain
the deliberately wrong scan:

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

Recovery succeeds because the PCE is present; attribution fails because the scan
is wrong. `why.json` names `scan_direction`. Change `--explain-view` to `recovery`
to inspect that assignment. All inputs are synthetic; no provider key is needed.
Use fresh output paths when repeating the exercise.

Explanations consume the actual decision ledger. `one_to_one_competition` names
the fact that consumed a compatible candidate; `fact_sources` retains original
paths when linked redundant assertions merge. Do not implement a second matcher
in an explanation, plot or export.

## Check a scoring change

Use the relevant scientific cases, then run the full checks in
[CONTRIBUTING.md](https://github.com/lamalab-org/perla-extract/blob/main/CONTRIBUTING.md).
Expected results must come from the stated scientific meaning, not regenerated
scorer output. Pair equivalent cases with wrong-unit, wrong-owner, wrong-context
and multiplicity counterexamples.

```bash
PYTHONPATH=.:src python -m pytest -q \
  tests/study_extraction/test_quantities.py \
  tests/study_extraction/test_quantity_pipeline.py \
  tests/study_extraction/test_scoring_semantics.py \
  tests/study_extraction/test_scoring_explanation.py
PERLA_RUN_REFERENCE_RELEASE_TESTS=1 python -m pytest -q -ra \
  tests/study_extraction/test_reference_release.py
PYTHONPATH=.:src python -m examples.scoring.validate \
  --release data/study_extraction/ground_truth/v2/release.json \
  --output /tmp/scoring-controls.json
```

The control report records applicable, passed and inapplicable cases. It must not
hide a required scientific case because parsing failed. `reference_audit.py`
checks release integrity and coverage declarations; `validate.py` applies
controlled perturbations. They share one reference-self-check policy. Frozen
references remain unchanged, including any explicitly flagged contradictions.

The current format and policy versions are recorded in reports and the scientific
specification. Regenerate older reports before combining scores; there is one
runtime scorer. Archived inputs load with unknown defaults rather than inferred
context. A perfect self-score does not prove reference completeness or accuracy.
For independent expert calibration, use the separate
[validation procedure](../methods/validation-and-scale.md#prepare-a-sample-for-independent-experts).

## Replay saved extractions

Use the existing results CSV with `paper_id` and `run_directory` columns:

```bash
PYTHONPATH=.:src python -m examples.scoring.replay \
  --runs path/to/results.csv --references data/study_extraction/ground_truth/v2/dev \
  --output /tmp/perla-baseline
```

Run this on the baseline checkout. Then run the revised checkout in the **same
Python/dependency environment**, adding `--baseline /tmp/perla-baseline` and
choosing a fresh output directory. Keep scorer code unchanged during each replay.
The runner verifies original run IDs, sealed inputs, article/SI bindings, roster,
settings and dependencies before comparing results.

`manifest.json` accounts for every attempt and unavailable input; unavailable
inputs fail the command. `field-deltas.csv` records both views and all denominators.
`decision-changes.json` records gained/lost credit and changed assignments by
original path with before/after diagnostics. Preserve failed and partial rows;
a score for a failed extraction does not make it complete.

Rescoring cannot measure improvements to parsing or prompts. Those need a paired
extraction experiment under a frozen scorer. The latest
[410-attempt replay report](https://github.com/lamalab-org/perla-extract/tree/main/benchmarks/test/replays/2026-11-26)
records observed changes and remaining limitations.
