# Maintain the codebase

See the [readiness review](readiness.md) for the remaining study blockers.

Start with the [offline scoring example](../workflows/scoring-example.md): change
one reported value and follow its lost credit. Then try the request-planning exercise
below. Both use invented data and need no provider key. Run commands from the
repository root with the environment activated. For pipeline development, install
`pip install -e '.[dev]'`; add `[docs]` to build documentation and Node.js 20+ to test
the reviewer interface. Dependencies must already be installed for offline use.

## Find the part you need

You do not need to read the whole repository to run an experiment. Its public entry
point is `perla-experiment`; it calls the same extractor and evaluator used separately.
The paths below are relative to the repository root.

| Responsibility | Start reading | Boundary to preserve |
| --- | --- | --- |
| Experiment inputs and design | `src/perla_extract/study_extraction/experiment_plan.py`, `reference_dataset.py` | Verify sources and references before creating output |
| Experiment execution and resumption | `study_extraction/experiment.py` → `experiment_state.py` | Preflight all inputs; reserve before requests; retain failed and uncertain slots |
| Extraction stages | `study_extraction/workflow.py` under the same package | Each stage records its result and fallback |
| Scientific record structure | `study_extraction/models.py` | Preparation, measurement and aging are different contexts |
| Models, limits and provider requests | `study_extraction/configuration.py` → `request_policy.py` → `client.py` | Resolve one policy for planning and execution; missing usage stays unknown |
| PDF text and evidence | `study_extraction/source.py` → `spans.py`, `transport.py` | Source IDs and quotations remain traceable to the supplied PDFs |
| Scientific scoring | [Scoring maintainer guide](scoring.md), then `study_extraction/evaluation.py` | Match owners separately from values; use one credit ledger for all summaries |
| Experiment results | `study_extraction/comparison.py` → `figure_data.py`, `experiment_summary.py` | Present saved scores without implementing another scorer |
| Export | `study_extraction/nomad.py`, `compatibility.py` | Project supported fields; retain other context in notes and report conversion losses |
| Reference review | [Reviewer guide](reviewer-ui.md), then `review_workbench/study_review.py` | Keep drafts, saved revisions and reference exports distinct |
| Paper discovery | `src/perla_extract/papersbot/cli.py` → `bot.py` | Discovery and acquisition do not establish extraction accuracy |
| Historical flat schema | `src/perla_extract/pydantic_model_reduced.py` | Compatibility consumer only; new extraction uses `StudyExtraction` |

`tests/` checks the Python package; `review_workbench/tests/` also exercises browser
modules. `data/study_extraction/ground_truth/` contains frozen references, while
`examples/` contains runnable demonstrations. The reference directory name `v2` is
a historical path, not a choice of scoring algorithm. Use the current format and
read the manifest for each reference's coverage and provenance.

The normal method is a direct call followed by source checks. Optional reading,
refinement, repair and enrichment require an explicit switch and a larger request
budget. `configuration.py` resolves this once for both Python and CLI users.
`scientific_settings()` projects enabled stages for comparison; the complete raw
configuration still appears in each run for debugging and provenance.

## Trace one request limit

```bash
PYTHONPATH=.:src python -m examples.extraction.plan --output /tmp/perla-planning-example
```

Choose an unused output path. The command creates its own PDF and runs the normal
parser and request planner twice. `fits/report.json` has status `dry_run`;
`blocked/report.json` has status `failed`. Both have zero provider requests.
Open `run_configuration.json` beside each report and find
`resolved_stage_policies.assemble.deployment.max_input_tokens`. The blocked plan
has an input allowance of one token. Its report explains why the same serialized
scientific request cannot fit. This exercise establishes planning behavior, not
extraction quality or a model's actual context capacity.

To change a stage's model, generation settings or context allowance, start in
`configuration.py`. `ExtractionConfig.stage_policy()` resolves inheritance once,
including the output reservation and shared input cap. `request_policy.py` exposes
that resolved policy to both planning and dispatch. An estimate includes the
system message, evidence, response schema and framing. Retry instructions consume
additional input and are checked again. A character-based estimate is not a model
tokenizer; use a local tokenizer when its accuracy matters.

## Follow an extraction

Read `_run_extraction()` in `workflow.py` first. Its operations are:

```python
source = prepare_source(config)                 # parsed evidence and request plan
reading = read_source(source)                   # optional notes; full-source fallback
built = build_study(reading)                     # one study, optionally refined
corrected = correct_study(built, source)         # citation fixes and guarded patch
final = finalize_study(corrected, source)        # grounding, validation, coverage
exports = interpret_and_export(final, source)   # optional proposals and exports
report = summarize(source, reading, final, exports)
```

`workflow_results.py` names the inputs passed between these operations. It does
not own model settings or duplicate accounting. `extraction_prompts.py` owns the
reading/assembly instructions and scientific prompt fingerprint.
`spans.py` numbers the passages visible to a request; `transport.py` translates
those integer references back to exact public block IDs and quotations. Selected
catalogs can have gaps, so decoding checks membership as well as numeric bounds.
Keep this mapping mechanical and reject unknown references.
`source_reading.py` fits windows and combines repeated notes with provenance.
`correction_checks.py` owns correction selection and source-claim retention. `quantities.py` interprets complete reported quantities
for validation, scoring and export; `quantity_specs.py` holds their checked names
and conventions. `study_outputs.py`
keeps optional interpretations separate from reported facts. `run_reporting.py`
serializes existing decisions rather than running scientific checks again.

`guidance.py` owns the scientific boundaries shared by reading, assembly and
correction. Explicitly shared measurement conditions belong on every observation
covered by the source statement, with scope evidence and narrower exceptions.
Source arm codes belong in device labels; distinguishing preparation belongs in
`reported_properties` or `subject_context`. A reused design code alone does not
establish the same physical specimen. These rules use the existing schema.

When testing a guidance change, pin the source, model, schema and scorer. Vary one
policy at a time and retain failed attempts and collateral field changes. The
workflow request tests check that the shared policies reach each relevant stage
exactly once; a live ablation is still needed to measure model behavior.
Treat stronger guidance as a behavioral hypothesis, not a validated fix. Check
whole-roster completion and other scientific fields before promoting a change.
Inspect context diagnostics as well: an unrecognized protocol name can change
attributed credit even when the raw quantity and unit are unchanged.

A citation check and reader-note coverage check are integrity constraints. They
cannot prove that the extracted scientific attribution is correct. For example,
a wrong device owner can still carry a genuine quotation. Evaluate scientific
accuracy against independently reviewed references.

## Diagnose a failed or expensive run

Open `report.json`. Its `stages` list contains the same outcomes as `stage.finished`
in `events.jsonl`, including handled failures and their fallback. A failed
refinement may retain its draft. A failed reader may fall back to complete source.
A failed optional interpretation remains failed even when the study can be exported.
The overall run status summarizes usability; it does not turn every stage green.
An absent terminal event means the process may have stopped before recording its
outcome. Do not infer successful completion from an existing prediction file alone.

Use a failing call's `slug` to locate its files in `requests/`. Each logical call
has one or more provider attempts; invalid responses and retries can still cost
money. `accounting.py` owns typed call/attempt records, and both report totals and
budget checks read those records. Cache reuse creates no provider attempt or new
charge. Missing provider usage remains unknown rather than zero. `client.py`
coordinates cache lookup, dispatch, decoding and retry decisions; its `_live()`
boundary is the seam used by failure-injection tests.

If invalid JSON has `finish_reason: length`, the error names the configured output
allowance and the raw response file. It is not retried with the same limit. Inspect
that response before raising the allowance: an unfinished useful answer and a model
repeating whitespace need different interventions.

`budget.provider_requests` counts conservative request reservations;
`usage.provider_attempts` counts attempted provider work. A local write failure
before dispatch can consume a reservation without creating a paid attempt. Use
the usage accounting fields when comparing experimental cost.

`observability.py` writes local events before optional trace export. `tracing.py`
maps these events to Langfuse and never initiates model calls. A tracing failure
disables that exporter and records `telemetry.failed`; it does not erase the local
journal. [Open models and tracing](../workflows/open-models.md) explains activation.

## Maintain experiment checks

`comparison_study.py` validates the declared papers, repetitions and arm values.
`experiment.py` derives that declaration from the user's single TOML, freezes the
roster, and calls the existing extractor, scorer and comparator. `--check` validates
all inputs without creating output. The runner has no independent scoring logic.
`comparison.py` verifies saved artifacts, checks the observed settings against that
declaration, and computes paired results. `figure_data.py` exports those results
without rescoring them. Keep the declared cohort separate from the observed roster:
deriving both from successful runs would hide omitted papers. A rejected design
retains available accounting but cannot produce a paired quality conclusion.

`examples/scoring/sample_review.py` prepares source-linked fields for independent
expert calibration using the score's actual credit ledger. Its reviewer files have
blank judgments; the analyst key holds scorer decisions and sampling probabilities.
See [expert sampling](../methods/validation-and-scale.md#prepare-a-sample-for-independent-experts).

## Explain a score and change a reviewer field

Run the [worked scoring example](../workflows/scoring-example.md) to obtain a
reviewed reference, controlled mistakes and known expected counts. Use the
[score explanation guide](scoring.md) to inspect the exact fact that lost credit.
Equality, owner matching, condition matching and assignment competition are
separate causes; changing a tolerance to fix an ownership error changes the wrong
scientific rule. Expected counts should be derived by hand, not regenerated from
the evaluator being tested.

Follow [Change the reviewer UI](reviewer-ui.md) to launch the synthetic paper,
correct its family link, and test form/JSON editing. It identifies the owners of
the draft, HTTP requests and saved revision. Frontend tests import these modules;
Node is required in CI so those tests cannot disappear behind a skip.

## Choose a focused check

| Change | Check | Evidence it provides |
| --- | --- | --- |
| Stage setting or limit | `tests/study_extraction/test_configuration.py`, `test_request_policy.py`, `test_stage_outcomes.py` | Planning and execution resolve the same input/output allowance |
| Retry, cache, budget or trace | `test_client.py`, `test_observability.py`, `test_stage_outcomes.py` in the same directory | Paid attempts survive failure; fallback outcomes stay visible |
| Scientific equivalence or pairing | The focused scorer tests and `test_reference_release.py` | Controlled contrasts retain their intended credit |
| Reviewer edit/save transition | `review_workbench/tests/test_review_modules.py`, `test_review_state.py`, `test_demo.py` | Draft, revision and persistence behavior agree |

Run the full checks in [CONTRIBUTING.md](https://github.com/lamalab-org/perla-extract/blob/main/CONTRIBUTING.md)
before submitting. Repeat the actual browser journey for UI changes: module tests
do not establish visual usability. These exercises are an onboarding contract to
try with new maintainers, not evidence that a human onboarding study has happened.
