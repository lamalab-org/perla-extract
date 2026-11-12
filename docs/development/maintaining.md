# Maintain the extraction pipeline

Start with three small tasks: inspect a request limit, explain lost scoring credit,
and save a reviewer correction. Each uses invented data and needs no provider key.
Install `pip install -e '.[dev,docs]'` and Node.js 20 or newer first. Run commands
from the repository root. Dependencies must already be installed for offline use.

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
`source_reading.py` fits windows and combines repeated notes with provenance.
`candidate_selection.py` owns the refinement acceptance rule. `study_outputs.py`
keeps optional interpretations separate from reported facts. `run_reporting.py`
serializes existing decisions rather than running scientific checks again.

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

`budget.provider_requests` counts conservative request reservations;
`usage.provider_attempts` counts attempted provider work. A local write failure
before dispatch can consume a reservation without creating a paid attempt. Use
the usage accounting fields when comparing experimental cost.

`observability.py` writes local events before optional trace export. `tracing.py`
maps these events to Langfuse and never initiates model calls. A tracing failure
disables that exporter and records `telemetry.failed`; it does not erase the local
journal. [Open models and tracing](../workflows/open-models.md) explains activation.

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
