# Extraction and scoring revision plan

**Implemented for review, 26 November 2026.** Baseline production commit:
`8a2d73c704da6cc1190ae91da82e0813de86f901`. This pass reviewed evidence parsing and
selection, corrections, quantity interpretation, identity/field scoring, exports
and their tests. The implementation changes production code while preserving
scientific inputs; no paid experiments have been run.

The baseline remains parsed evidence → one structured extraction → deterministic
checks → scoring/export. Preserve the current study schema, public commands and
human references. Implement the changes below in separate commits within the
existing PR; review the PR before further paid experiments.

## What the review established

| Priority | Finding | Evidence and scope |
| --- | --- | --- |
| P1 | Selected tables can lose linked captions/notes; equal grids can be deduplicated despite different scan captions | Reproduced integration counterexamples in `claims.py:344–349`, `source.py:836–856` and `spans.py:174–178`. Reader selection is optional; shared dedup also affects the direct path. No dangling links were found in 14 inspected saved documents; prevalence is unknown. |
| P2 | PyMuPDF can discard unique prose beside a table | Synthetic geometry case at `source.py:240–245`: a mixed block loses an outside 24% result. This concerns the optional backend. |
| P2 | Quantity interpretation differs between scoring, validation and export | Actual Solar RRL humidity: raw `50% RH`, units `% RH` versus `%`, recovery 0/2. Aligning spelling in a diagnostic copy recovers 2/2; other attribution failures remain. |
| P2 | Scalar export can erase qualifications or choose conflicting candidates by order | Actual Alloy PCE `∼14 %` becomes an unqualified scalar. Bounds/ranges/uncertainties and conflicting stability summaries also fail synthetic controls. Rich source data remains available; the standalone projection is lossy. |
| P1 | Outcome text can identify its own specimen | Synthetic PCE-number labels resolve otherwise ambiguous ownership. All label, variant, selection and sample-code routes require the same policy. |
| P1 | Correction retention confuses presentation with scientific identity | Actual Alloy D13: “transient photovoltage” → “TPV” marks unchanged ∼1.38 μs as lost. Synthetic equal-valued scans can disappear, and `Co/CO` or charge/subscript changes can pass. |
| P2 | Audits obscure decisions and tests can omit their own blind spots | Final checks overwrite refinement rejection reasons. Generated unit controls select inputs using production parsing, so unsupported forms can be excluded. |

The Alloy refinement also changed family grouping and introduced a validation
issue. The label example does not establish that accepting the whole refinement
would improve accuracy. The 20%-versus-2 contradiction and outcome-label leakage
remain synthetic; they were not established as causes of errors in saved runs.

## Implementation order and boundaries

### 1. Freeze inputs and establish independent expectations

- Record prediction, reference and source hashes, original run IDs, code version
  and dependency versions for the saved replay. List every unavailable input.
- Regenerate the baseline with the reviewed scorer on these exact inputs; do not
  compare different reference revisions or silently mix historical score versions.
- Use the same dependency environment for baseline and revised scoring, retaining
  its package freeze. A Pint/dependency upgrade is a separate experimental factor.
- Extend the existing synthetic example and ordinary parametrized tests. Write
  expected scientific meanings and decisions explicitly. Convert reproduction
  scripts into tests of the desired behavior as each fix is implemented.
- Preserve original runs and reports. Refactor-only changes must retain identical
  scientific inventories, decisions and assignments before policy fixes begin.

**Exit:** the baseline is reproducible, required cases are enumerated, and the
expected results are not computed by the production parser or scorer.

### 2. Extract one shared quantity implementation

The implementation moves the previous quantity interpretation/equality into `quantities.py`;
keep its Unicode protections and conservative complete-claim grammar. Add one
small, data-only `quantity_specs.py` with supported quantity IDs, existing checked
aliases, dimensions and conventions. Move common alias definitions rather than
copying them. Material chemistry and protocol/ownership rules retain their owners.

```text
quantity_specs.py                 units.py
names, dimensions, conventions    numeric unit conversion
               \                 /
                  quantities.py
       complete claims, consistency, projection
               /       |        \
         validation  scoring   export/enrichment
```

`units.py` accepts numbers and unit strings. Move or replace its rich-value
convenience helpers so it never imports `quantities.py`. The quantity core imports
neither the scorer nor its vocabulary; vocabulary consumes shared definitions.
Migrate every caller and remove superseded parsing paths and temporary wrappers.

Return an interpretation and a stable reason: normalized quantity, preserved
literal/unresolved claim, or explicit contradiction. Keep raw text and citations
unchanged. Cache by quantity kind and raw fields, including units; the same text
under a different quantity kind must not reuse the wrong interpretation.

**Exit:** exact refactor parity; one interpretation implementation; no circular
imports, new rule language, new model stage or new user configuration.

### 3. Apply quantity semantics to all consumers

Start with PCE, FF, Voc, Jsc, temperature, duration, thickness, area, spin speed
and relative humidity. Keep generic supported units for other quantities; unknown
meaning stays explicit. Each new supported quantity needs an equivalent case and
a scientifically different counterexample.

- Interpret `RH` as a qualifier only for recognized relative humidity. Both
  `50% RH` forms become 0.5 dimensionless humidity; arbitrary `RH` unit strings
  are not globally rewritten. Humidity, PCE and FF remain different concepts.
- Compare raw and supplied parsed values as physical quantities when both carry
  complete explicit units: `20 ms` and parsed `0.02 s` are consistent; `20%` and
  parsed `2%` are not. Internal conversion roundoff is separate from scoring's
  unchanged **1% relative, zero absolute** tolerance.
- Preserve the approved percentage-point convention for unitless PCE/FF:
  `0.2` means 0.2%. An explicit dimensionless fraction 0.2 means 20%. Record the
  convention used; do not select it by value size or the paired reference.
- Retain ranges, uncertainty, approximation and statistical qualifiers. Unknown
  or contradictory claims remain in the scoring inventory and its diagnostics.

Unknown but consistent literals retain the existing exact-literal comparison.
An explicit contradiction cannot earn numerical-field credit through convenience
values or exact repetition of the contradiction; literal agreement may be shown
as a diagnostic only. Flag contradictory reference quantities prominently and
withhold a clean quantitative-accuracy conclusion until they are resolved through
the reference process. Never silently exclude or rewrite those reference facts.

Add stable issue codes to `validation.py`; retain typed locations internally and
readable paths/messages in artifacts. Update `finalization.py` to act on codes,
with an explicit allowed-removal set. New quantity issues must not cause automatic
deletion. Keep citation grounding counts distinct from quantity consistency.
Use existing stage results to distinguish extraction completion from partial export;
one unprojectable field must not erase the extraction or all of its scores.

Export from the shared interpretation. Bounds/ranges cannot become exact scalars.
A supported uncertainty/approximation may supply a center only when the raw claim
and qualification are retained in the target's notes; otherwise retain it as
unprojected data. Contradictions never supply numeric output. Preserve the raw
claim for projected metrics too, not just unsuccessful projections.
Supported means a recognized complete claim form; an arbitrary literal cannot
acquire a numeric center merely because a convenience number was supplied.

For stability summaries, collect candidates per destination field. Export a
provably unique result; collapse equivalent copies using conversion roundoff only.
Withhold conflicting values with a reason. Reordering conflicting candidates
must not change the summary. Keep genuine checkpoint sequence and protocol
differences; do not infer chronology across aging-stage time resets.

**Exit:** humidity recovery passes the known case; source grounding cannot hide a
numeric contradiction; scoring and export preserve the same scientific claim.

### 4. Preserve evidence through selection and deduplication

- In `claims.py`, retain explicitly linked table captions/notes before discarding
  blocks. Follow existing links with a visited set, preserve source order, and
  retain complete relevant headers. Unrelated nearby text is not automatically
  shared context. If required context exceeds the request budget, report it.
- In `source.py`, conservatively preserve tables and their linked context during
  deduplication. Avoid a scientific equivalence engine for token savings. Any
  remaining ID canonicalization must preserve references explicitly.
- Verify retained context-link targets after selection/deduplication. Missing
  links are observable issues, not silently emptied metadata. An older document
  with no context links has unknown coverage; absence alone is not corruption.
- For PyMuPDF, preserve blocks containing prose outside a table; suppress only
  effectively contained duplicates. Prefer some repeated text to unique data loss.

**Exit:** complete prepare/selection → spans → request payload → citation decoding
tests preserve the required evidence and distinguish equal-valued experiments.
Parser fingerprint/cache invalidation must reflect parser changes.

### 5. Correct identity, retention and decision explanations

Create one identity-text policy before **all** identity consumers, including
sample-code extraction, family features and cohort/specimen descriptors. Preserve
explicit source designations, meaningful numeric codes, chemical/treatment
distinctions and protocol evidence. Recognized outcome assertions cannot resolve
an otherwise ambiguous owner. Uninterpretable identity prose remains unresolved.
Keep one-to-one assignment and the separate recovery/attribution views.

For corrections, compare complete scientifically normalized claims with their
measurement/statistical scope and multiplicity. Citation spelling/location and a
display-label change are not claim identity; their validity is checked separately.
Use scientific Unicode/case handling, not citation-search normalization. Permit
exact redundancies under the existing duplicate contract while preserving distinct
specimens, scan directions and statistics.

Use existing IDs as edit anchors, not scientific identity evidence. Separate
retained content, ownership edits and semantic deletions in the change audit.
An explicit ownership edit that retains content and passes link/citation/quantity
checks is not value loss. It remains a model-proposed attribution, not a proven
scientific improvement. Ambiguous edit correspondence is reported rather than
guessed: retain the earlier candidate and save the rejected proposal. Automatically
accept only defined edits with one-to-one retained-claim accounting and the existing
exact-duplicate/source-unsupported removal policies. Other semantic deletions stay
rejected proposals until a separate correction policy is validated; reducing issue
counts alone cannot justify them.

Store proposal selection and final integrity check as separate named decisions
in the existing audit. Preserve the original rejection reason and affected paths.
Use precise public wording for source checks rather than implying expert accuracy.
Keep optional corrections experimental until a paired development study supports
their cost/quality tradeoff.

**Exit:** outcome injection cannot break identity ties; harmless label/citation
edits survive; deleting a distinct scan or changing chemical meaning is detected;
retained drafts keep the rejected proposal's explanation.

### 6. Finish the testing path, replay and documentation

Reuse the worked example, existing evaluator, comparison ledgers and CI. Add only
a thin saved-replay utility using existing run manifests/rosters and
`examples/scoring/score_changes.py`; do not introduce another scorer or input
format. Write per-paper evaluations through the current evaluator, then aggregate
compatible reports through `perla-evaluate-dataset`.

Produce one summary by paper, field and scoring view; list every gained/lost fact,
changed assignment and inventory change by original path and rule. Explain
canonical duplicate merges and preserve their source aliases. Missing runs remain
visible. **Higher F1 is not a pass criterion; unexplained changes are a failure.**

Make the scorer/normalization contract version explicit once the policy is frozen.
Keep historical reports readable and immutable. Use one authoritative version
source in current documentation. Shared scientific instructions already live in
`guidance.py`; retain that ownership and preserve prompt bytes. Any actual prompt
shortening needs its own measured change. Update the existing maintainer/testing
pages, without another tutorial set.

## A testing procedure students can understand

Choose the layer by the question being answered. Passing a lower layer does not
establish the claim tested by the next one.

| Layer | Question | Pass/fail and result to inspect |
| --- | --- | --- |
| Required scientific cases | Are known meanings treated correctly? | Every declared case runs; explicit expected values, dimensions, credit and reason agree. A rejected parse cannot make its case “not applicable”. |
| Offline workflow integration | Do decisions survive the connecting code? | Real selection, transport, validation, correction, scoring and export; stub only external boundaries. Zero provider calls, intact raw evidence and expected stage decisions. |
| Real parser/schema integration | Do installed dependencies preserve our authored content? | Known table cells, merged header, blank cell, caption/footnote associations and native text survive. OCR is named separately. A required integration for affected behavior must pass; skipping it blocks acceptance. |
| Frozen saved-output replay | What changed on identical extracted data? | Fixed input hashes/roster; complete per-path accounting and explained deltas. No missing runs, hidden denominator loss or overwritten reports. |
| Paired live development experiment | Did extraction improve at acceptable cost? | Frozen scorer, same papers/model/settings except the declared intervention, failures/costs retained, paired paper-level summaries. Small pilots are not strong statistical evidence. |

### Mandatory cases and independently specified expectations

| Case | Expected scientific behavior |
| --- | --- |
| RH `% RH` versus `%` | Both represent 0.5 relative humidity in the known example; check dimensionless meaning directly. |
| PCE 20% versus explicit fraction 0.20 | Same quantity; unitless PCE 0.2 separately means 0.2%, not 20%. |
| 50% humidity versus 50% PCE | Different concepts despite equal magnitudes/dimensions. |
| Celsius/Kelvin and temperature uncertainty | Offset the absolute temperature; scale uncertainty without an offset. |
| Range, bound, uncertainty, approximation | Preserve the complete assertion through scoring and export. |
| Raw20%/parsed2%; raw20ms/parsed0.02s | First is a contradiction; second is physically consistent with explicit units. |
| Same value, wrong scan/owner/statistical level | Recovery may pass while attribution fails; expected results name both views. |
| Equal-valued forward/reverse measurements | Both survive; deleting one is information loss. |
| TPV label/citation edits; Co/CO and charge/subscript changes | Presentation edits retain the claim; scientific changes do not. |
| Outcome text versus genuine `Device A20` | Outcome text cannot resolve identity; genuine codes remain usable. |
| Table caption selection, equal grids/different scans, mixed native block | Explicit context and distinct scientific evidence survive the full chain. |
| Conflicting stability candidates; unknown claims | Conflicting summaries are withheld and order invariant; unresolved claims remain counted. |

Use ordinary parametrized tests and small authored fixtures. Expected conversions
come from explicit arithmetic/constants, not a call to the production unit parser.
Keep broad generated controls, but report applicable/passed/inapplicable coverage
alongside required cases. Self-score or round-trip invariance alone cannot detect
two identical physically wrong interpretations.

Extend `examples/scoring/expected.json` and the existing demonstration rather than
building a fixture framework. It already checks independently written counts and
has a test requiring corrupted expectations to fail. A contract-test provider
sentinel makes accidental inference fatal; client transport tests explicitly replace
that boundary. Keep the remote cost map disabled in offline jobs.

For actual Docling integration, add a tiny authored table/caption PDF with known
content; test its real parsed associations. The existing opt-in smoke test disables
OCR and does not establish default OCR quality. Run the additional OCR case only
with its weights prepared, in a named integration job. Include its result when
parser behavior or a default-OCR quality claim is affected; a disabled-OCR smoke
test cannot support that claim. Require the pinned NOMAD integration when export
compatibility changes. Unrelated optional integrations may remain explicitly untested.

### Commands and what they prove

Run these commands from the checkout root after dependencies are installed;
choose new output directories. Required scientific cases now live in the ordinary
quantity, identity, correction and evidence tests.

Minimal worked example on Python 3.12, Linux/macOS:

```bash
python3.12 -m venv .venv-scoring
source .venv-scoring/bin/activate
python -m pip install -r examples/scoring/requirements.txt pytest
PYTHONPATH=.:src python -m examples.scoring.run --output /tmp/perla-example-new
```

This prints `PASS: five synthetic cases`. Read `summary.json` for expected versus
actual counts and environment versions. The example also checks numeric exports
and retained raw claims. Its dependency set omits provider SDKs and PDF models.

For package changes, use the development environment (`pip install -e '.[dev]'`):

```bash
LITELLM_LOCAL_MODEL_COST_MAP=True python -m pytest -q -ra tests/study_extraction
PERLA_RUN_REFERENCE_RELEASE_TESTS=1 python -m pytest -q -ra tests/study_extraction/test_reference_release.py
PYTHONPATH=.:src python -m examples.scoring.validate --release data/study_extraction/ground_truth/v2/release.json --output /tmp/perla-controls-new.json
```

`-ra` exposes skipped integrations. Read `calibration_coverage` in the control
report, not just its exit status. These archived human references retain their
historical path/metadata; their current scientific role is the human test set.
Do not use its score changes to choose paper-specific normalization rules.

Before merge, run the full repository suite, Ruff, mypy and strict documentation
build using the existing CI commands. Record required integration results and skip
reasons. No new labels or paid/provider LLM calls are needed for the first four
layers; prepared parser integrations can execute local neural models.

## After review

First freeze normalization/identity scoring and rescore saved runs. Evidence-path
changes require **new extractions** to measure their model effect; rescoring an old
extraction cannot reveal recovered source context. After PR review, compare control
and revised extraction paths on the same disjoint development papers with a fixed
model and paired repetitions. Then test cheaper settings under the same scorer.

Respect the existing campaign cap and prior reservations. Keep model-labeled
development agreement distinct from final human-reference accuracy; address the
remaining [study-readiness prerequisites](readiness.md) before unattended runs.
An LLM adjudicator is a later, separately validated experiment for unresolved
semantic cases, not a dependency of these deterministic fixes.
