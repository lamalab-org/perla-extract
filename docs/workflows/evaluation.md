# Scoring reference

`perla-evaluate` compares an extraction with a reference using fixed rules. It makes
no LLM or embedding calls. The score measures agreement with the reviewed records;
its usefulness depends on their correctness and completeness.

This guide uses three terms:

- Reference: the records you have reviewed against the paper.
- Prediction: the unchanged extraction you want to evaluate.
- Fact: one scored item, such as a PCE value, a layer material or an annealing
  temperature, together with the context needed to interpret it.

The scorer first pairs records, then compares their facts. It reports missing and
extra facts, wrong values and wrong associations separately. Record pairing is an
estimate: the scorer can pair two records incorrectly.

For the overall method, read [Evaluation method](../methods/benchmark.md).
For the steps from saved corrections to scores, read
[From corrections to a benchmark](review-to-benchmark.md).
The [worked example](scoring-example.md) runs without PDFs or an API key.

## Run a comparison

The repository includes a [30-paper frozen development release](https://github.com/lamalab-org/perla-extract/tree/main/data/study_extraction/ground_truth/v2).
Its corrected records support automatic reference agreement; unreviewed source
coverage remains a limitation even when the saved records were reviewed.
The prediction path below is an example; generate that run separately:

```bash
perla-evaluate \
  --truth data/study_extraction/ground_truth/v2/dev/10.1126--science.adf0194 \
  --prediction results/10.1126--science.adf0194 \
  --output results/10.1126--science.adf0194/evaluation.json
```

A frozen reference directory contains `ground_truth.json` and `manifest.json`.
The command validates the study against the current schema and verifies that the
reference's content hash matches its manifest. It records the paper ID, split,
reference hash, source hashes and source-manifest hash in the report.

Record IDs must be unique within each collection on both sides. Duplicate IDs stop
scoring: otherwise an identity map could silently collapse two devices or observations.
Fix malformed output and retain that failure in your run accounting; do not silently
drop failed papers from a model comparison. Legitimate extra records with distinct
IDs remain in the precision denominator.

These checks detect mismatched files. They do not prove that anyone reviewed the
reference. The scorer does not repeat adjudication or check the reference's
citations against the paper. Use the [review and export workflow](ground-truth-review.md)
to create the reference.

New references use format 4 and require a final verified decision for every record.
The loader also requires the manifest to declare completed adjudication and verified
validation with zero issues. Every exported manifest declares a
`completed_stage_names` list; reviewer assignments remain in the private review
history. These declarations are checked, not authenticated as independent review.
Only ground-truth artifact format 4 is supported. The evidence-document version
and hash bind the reviewed source. Producer schema versions and hashes describe
where the artifact came from; its payload must validate against the one current
`StudyExtraction`. No old field names or automatic schema upgrades are accepted.

For `--prediction`, prefer a complete run directory containing:

| File | What the scorer does with it |
| --- | --- |
| `extraction.json` | Loads the predicted study |
| `document.json` | Rechecks the prediction's citations and record links |
| `report.json` | Loads saved call, token, cost and timing totals |
| `run_configuration.json`, `run_manifest.json` | Binds the exact configuration, prediction, document and accounting to one run ID |

Citation checks do not determine whether a passage supports a claim scientifically.
Citation issues do not reduce the fact score automatically; inspect the validation
result alongside the score.
Run accounting comes from the saved report; the scorer does not audit provider bills.
When reference source hashes exist, the CLI requires the same main-paper and SI
hashes in the sealed prediction. `input_warnings` records conflicting extracted DOIs
and missing provenance. Hash agreement does not establish reference completeness.

You can pass a bare JSON file for either study. Bare references have no manifest
checks or review-status checks; bare predictions have no evidence-validation
or run-accounting results. Their scores are diagnostic; source compatibility is unverified.

Measurement conditions belong in `measurement_conditions` on an observation or
population statistic. For example, `{ "name": "irradiance", "raw_value": "100",
"value_number": 100, "unit": "mW/cm2", "evidence": [...] }` describes the light
during measurement. Preparation stays under processing/subject context and aging
under stability tests. Archived registered protocol entries in `metrics` remain
readable; no missing conditions are inferred.

`core_facts.diagnostics` explains each fact's rule, parsing status and overlapping
candidate blockers. Experiment exports include `scoring_diagnostics.csv` with the
same decisions and original source paths. Read it alongside both score views;
context availability is not an estimate of extraction correctness.

`unit_source` distinguishes units in a separate field, the raw value or a PV
field name (`PCE [%]`), plus missing/unsupported units and unparsed claims.
The CLI reports missing PV units and applications of the fixed convention on
each side. Unitless absolute PCE/FF are percentage points (`20` means `20%`;
`0.2` means `0.2%`). Explicit units take precedence, including a raw suffix or
dimensionless fraction. The convention is recorded as `unit_source=percentage_points`.
It applies symmetrically to references and predictions; input files are unchanged.
For PV values, an unreported reference method/scan does not contradict extra
prediction detail. Explicit contradictions and missing known reference conditions
still prevent credit. See the [exact context rules](../methods/scientific-scoring.md#treatment-identity-and-context).

Reports record their format, matcher and fact profile; the
[scientific specification](../methods/scientific-scoring.md) names the current contract.
Regenerate older reports from saved predictions before
combining scores. Outputs must use fresh filenames; previous scores are retained. Each report hashes both parsed studies, including IDs and array
order, so you can locate the inputs behind its field paths. These are hashes of
normalized JSON content, not the original file bytes.
When loading reports, checks reject impossible match counts or rates that disagree
with their counts. They also check scientific group totals, the group-average F1,
the one-to-one ledger of canonical facts and their source fields, and consistency across the
two fact-score views. These checks detect internally inconsistent reports; they
do not authenticate an author or prove that the claimed matches are correct.

Frozen releases retain the scorer versions used when they were published. Do not
edit their metadata to use a newer scorer. Generate new evaluation reports instead;
the reference stays fixed while the new report records the new scoring rules.

## What is scored

### Start with individual fields

`core_facts.fields` reports precision, recall and F1 for each scientific quantity,
using the current rich reference and schema. The CLI prints PCE, FF, Voc and Jsc;
their keys are `measurement:pce`, `measurement:ff`, `measurement:voc`, and
`measurement:jsc`. Other named fields use the same `namespace:quantity` convention.
`matched` is TP, `predicted - matched` is FP, and `truth - matched` is FN. A wrong
substitution contributes both FP and FN. Null placeholders earn no credit.

Each field uses the existing one-to-one fact ledger. Correct PCE can earn credit
when FF or an unrelated processing field is wrong. Its specimen/family must still
be paired without using the measured numbers, and its measurement type, scan and
statistical meaning must agree. Ambiguous ownership remains unresolved.

Bounded name normalization recognizes `PCE extracted from the J–V curve` and
population `average PCE`, preserving the method and statistic. Contradictions
between such a name and the structured record block credit. Relative improvement,
retention, population means and individual results are not interchangeable.

For these four PV quantities, the reference's stated protocol is required. Extra
prediction conditions are scored separately; an unannotated scan rate does not
invalidate an otherwise matched PCE. Free-text device variants and selection prose
are used for record pairing but do not veto an established correspondence again.
A reported reference champion status and preparation context remain requirements;
when the reference omits them, extra prediction details are not certified by the PV
score. Explicit AM 1.5G notation is normalized, including the word “irradiation.”

The CLI and experiment summary show the following default fields when present.
`important_fields.csv` selects the same entries from the full field ledger.

| Domain | Fields |
| --- | --- |
| PV performance | PCE, FF, Voc, Jsc |
| Device dimensions | Active area and masked active area, evaluated separately |
| Device/material identity | Absorber formula, constituents, layer materials, polarity, fallback raw stack |
| Material properties | Bandgap and layer thickness |
| Measurement | Irradiance and illumination spectrum |
| Processing | Operation, temperature, duration and spin-coating speed |

The exact keys and labels live in `scoring_fields.IMPORTANT_FIELDS`. This list
selects results for display. Linked duplicate formula/material assertions can merge
into one canonical material fact. Raw stack text is used only when structured
layers are absent. Process conditions retain their family, target and ordered
step, even when several operations share a field key such as `processing:duration`.
Unknown names remain visible in `field_scores.csv`.

Specimen pairing also reads explicit compact sample codes from labels/variants.
`Device A` agrees with `Device A, Figure 4/Table 1`, while A/B and B/A remain
different. Conflicting codes exclude a specimen pair. Within an established
family, linked measurement kinds provide additional evidence for a specimen's use.
A J–V specimen can therefore be distinguished from a specimen used for stabilized
power output. Repeating a measurement or changing its results adds no identity
evidence. `matches[].identity_evidence` records the sample codes and measurement
kinds used for each selected specimen pair. Equally plausible same-method
specimens remain ambiguous.

`core_facts.strata` separates measurement fields by individual/population level,
statistic and measurement type. Credited predictions are assigned to the reference
stratum; unmatched predictions retain their declared stratum. This distinction is
especially relevant to recovery, which can match across otherwise incompatible strata.
Dataset `core_fields_micro` sums field counts before calculating rates. It does not
average paper precisions. Experiment plotting tables include `field_scores.csv`
and `field_strata.csv` with paper, arm and repetition, including TP/FP/FN.

Always show completion and reference coverage alongside field scores. Missing
evaluations do not represent zero precision, and incomplete reference scope does
not establish that every unannotated prediction is false. Use the same paper roster,
reference hashes and score contract when comparing models. The current references
remain authoritative; reproducing an older benchmark is not required.

### Primary score: correct scientific facts

Use `core_facts` for correctly attributed scientific facts and
`core_facts.recovery` for information found anywhere in the paper. Both use one
canonical fact inventory and independent one-to-one assignments. Recovery can
match a population result stored as an individual measurement; attributed credit
still requires the correct specimen level, treatment and measurement protocol.

| Scientific group | Included claims |
| --- | --- |
| `performance` | Individual and population outcomes, protocol and statistical metadata |
| `material_device_identity` | Finished-material composition, layer materials/roles/forms, architecture and device polarity |
| `processing` | Recipe composition, fabrication operations/materials/conditions, specimen properties and structured treatment conditions |
| `stability` | Aging-test conditions, checkpoint times and outcomes |

Candidate matching uses a semantic concept key, never a report group/subfield.
Recipe versus finished material and absolute efficiency versus retention remain
different concepts. Unknown names stay literal and remain in the counts.

Some details affect how facts are counted:

- Structured layers take precedence over the raw stack string. If there are no
  layers, the scorer counts the raw stack string as one fact, falling back to
  architecture if the string is absent. It does not split the string into layers.
- Fully ordered layers align by material and relative order. Only links shared by
  all longest alignments establish correspondence; omissions remain unmatched.
  Roles, forms and properties also require the material to agree. Partial orders
  retain their explicit scopes.
- Fully ordered recipes align by operation and target in relative order, allowing
  gaps. A missing middle operation does not invalidate later retained steps solely
  because they were renumbered. Recognized duration/temperature conditions of an unambiguously aligned annealing
  step, and duration/speed of spin coating, do not require an optional material list.
  Concentrations, ratios and unknown conditions still require it to agree. Partially ordered recipes retain their explicit sequence/operation/target
  scopes; no missing sequence is inferred. Specimen-specific properties use the
  device's family and canonical treatment context (literal variant when no
  recognized treatment inputs are supplied).
- Equal properties and constituents repeated in a layer and its sole explicitly
  linked absorber count once. Equal recognized treatment inputs repeated in a
  device's structured conditions and reported properties also count once.
  `core_facts.fact_sources` retains all original paths
  for each canonical fact. Copies within one list or in different layers remain
  separate facts. Merging allows only conversion roundoff (16 machine epsilons
  relatively, zero absolute allowance), independently of matching tolerances.
  Conflicting quantities across the linked representations are
  flagged and cannot receive strict credit. No ambiguous absorber link is merged.
- Equal whole-material identities across a layer material, its sole whole-material
  constituent and its sole linked absorber formula also count once. Amounts and
  roles retain their own points. Explicit precursor roles remain recipe claims;
  precursor ratios never establish finished-film composition.
- Explicit sonication cleaning can be written in the operation or as `cleaning`
  with a `Method: sonication` condition. This counts as one operation, with both
  original paths retained. Generic cleaning without that method is not equivalent.
- Stability checkpoint times require matching test and checkpoint conditions.
  Checkpoint conditions require matching test conditions and time. Outcomes require
  matching test conditions, time and checkpoint conditions.

IDs, citations, titles, notes and display labels do not earn separate fact points.
Neither do absent, empty or `not_reported` scalar fields. Labels can still influence
record pairing; when several absorbers lack layer links, absorber labels also help
distinguish their context. Champion status constrains performance matches rather
than earning its own point.

When structured treatment identity is absent, two labels carry scientific identity: a population's `label`
and a stability test's `specimen_label` when it has no explicit `device_id`.
These must agree after Unicode, minus-sign and whitespace normalization. For example,
two populations called `control` and `treated` must not trade their PCE values and
still get full credit, even if their family and sample size agree. An explicitly
device-linked stability test uses the device link instead of its display label.
This rule is conservative: `control` and `untreated cells` do not automatically match.
When both population records supply `subjects`, their treatment conditions, treated
material and treatment stage establish identity instead of prose labels. Conditions
use the ordinary unit/tolerance rules. Shared subject sets retain multiplicity; one
assertion applying to several cohorts does not become several predicted facts.
`error_bar_type` is separate from the outcome's `statistic_type`: SD error bars do
not establish a mean, nor turn the outcome into a standard deviation.

For quantities, a match requires the property name, value, units and recorded
context to agree under the rules below. Non-numeric facts use the corresponding
text or exact-value rules. A match also requires no detected ambiguity affecting
that fact. Extra facts reduce precision; missing facts reduce recall.

Individual performance facts belong to their explicitly linked device, not the
observation ID. Repeated observations are compared as one-to-one multisets under
the same complete scientific context. Splitting a metric array across observation
records does not change credit; missing/ambiguous devices and different protocols
still prevent it. This score does not evaluate joint measurement vectors or
replicate pairing. Cohorts and stability tests retain their existing identity rules.

For example, suppose the reference reports PCE = 20% and the prediction reports 21%
for the same paired device and scan. The counts are `truth=1`, `predicted=1` and
`matched=0`. The wrong value counts as both an extra predicted fact and a missing
reference fact. It does not earn credit for merely mentioning PCE.

One prediction fact cannot match several reference facts. Repeating a correct value
does not earn more credit. The report lists each matched pair and each unmatched
fact by its canonical JSON path. Any original path in `fact_sources` can also be
explained. To explain a selected path, use `--explain-truth`
or `--explain-prediction` with `--explanation-output`. The separate artifact names
value/context differences, ambiguity sources, or the competing field that used a
match. See [Explain and change scientific scoring](../development/scoring.md) for
a no-model-call example and the Python API.

### Read precision, recall and F1

```text
precision = matched / predicted
recall    = matched / reference
F1        = 2 × matched / (predicted + reference)
```

A rate is `null` when its denominator is zero. For example, an empty prediction
against a nonempty reference has undefined precision, zero recall and zero F1.
If both sides are empty, all three rates are undefined.

- `core_facts.groups` gives counts and scores for each scientific group.
- `core_facts.micro` pools facts across groups before calculating the scores.
- `core_facts.micro.f1` is the per-paper summary used by experiment comparison.
  Domain and subfield breakdowns show which kinds of facts contribute to it.

A group with reference facts but no predicted facts scores zero. A group empty on
both sides is left out of the average. Equal group weighting is a reporting choice,
not a claim that every group has equal scientific importance. Show the group counts
alongside the average.

### Subfields and their keys

`core_facts.subfields` partitions the same facts into the categories below. Each
contains `truth`, `predicted`, `matched`, `precision`, `recall` and `f1`; every match
also names its subfield. The recovery and attributed views have identical
subfield denominators. Reporting bins do not determine matching or aggregate weights.
Subfields do not add weights, and broad `composition` is not pure formula accuracy.

| Subfield | Included keys or source fields |
| --- | --- |
| `material_identity` | Layer `material`; absorber `formula`; whole/non-recipe constituent `name`, after linked deduplication |
| `composition_amounts` | Non-recipe constituent `amount`, including its reported basis |
| `composition_properties` | Checked `chlorine in resulting perovskite film(s)` property |
| `material_roles` | Non-recipe constituent `role` |
| `recipe_composition` | Constituent name/role/amount with explicit precursor or precursor-additive role; composition properties named `precursor…` |
| `material_properties` | Remaining absorber/layer properties and unfamiliar composition properties |
| `device_structure` | Layer `role`, `material_form`, family `polarity`, raw stack fallback |
| `photovoltaic_metrics` | Parsed individual or population PCE, Voc, Jsc, FF and their checked name aliases |
| `measurement_protocol` | Recognized conditions in individual or population `metrics`: illumination/intensity/spectrum/standard, scan rate/voltage range/voltage step, measurement temperature/atmosphere/condition, storage before measurement/in glovebox |
| `other_measurement_fields` | Other individual metrics, including retention; unfamiliar keys remain literal |
| `relative_performance` | Parsed efficiency-retention quantities, from either measurement container |
| `qualitative_observations` | Literal measurement statements without numerical cues |
| `unparsed_measurement_claims` | Literal measurement claims containing numerical/bound cues; not silently treated as parsed quantities |
| `statistical_metadata` | Sample size and error-bar statistic, including `error_bar_type` |
| `stability_protocol` | Test/checkpoint conditions and checkpoint time |
| `stability_outcomes` | Checkpoint outcomes |
| `processing_operations` | Step operation, including an explicitly equivalent method representation |
| `processing_materials` | Step `materials` entries |
| `processing_conditions` | Step conditions (except a method merged into its operation), specimen `reported_properties` and structured treatment conditions |

`scoring_fields.py` is the classifier; `scoring_protocol.py` owns the exact protocol
aliases. Names ignore whitespace, underscores and hyphens. Unknown concepts remain
literal, counted in their stated container. `η` and `efficiency` mean PCE only for
`jv_scan`, `stabilized_power_output` or `certified_measurement` observations.

A wrong or omitted measurement condition withholds strict outcome credit; recovery
credit can still show recovery of the number. Conditions themselves are scored in
the device/cohort context without depending on the other conditions. No condition
is inferred from a neighboring record or borrowed from the reference.

Experiment output includes `figure-data/subfield_scores.csv` with these same counts.
Filter `quality_available` for verified runs: incomplete attempts retain diagnostic
rows and accounting, but cannot produce a paired quality result. Structural validation
issues block comparison; citation issues and validation coverage remain visible in
`runs.csv` and the summary.

### Separate value recovery from attribution

The report has two views:

| Report field | Meaning |
| --- | --- |
| `core_facts` | Correct value with compatible scientific context and unambiguous attribution |
| `core_facts.recovery` | Correct concept/value recovered anywhere within the paper, including across individual/population containers |

Each uses the same denominators and its own one-to-one assignment. A recovery
match cannot establish a specimen's identity. Equal outcomes cannot break an
identity tie. Do not combine the assignments or multiply the scores.

Sample sizes and error-bar types are independent metadata facts; a wrong outcome
statistic does not erase a correctly attributed count. Conflicting reported counts
still block outcome attribution. A missing count loses its own point only.

For example, 93% retention attached to an individual device can earn recovery
credit against a population reference while receiving no attributed outcome credit.
Absolute PCE of 93% cannot earn that recovery credit. Both decisions appear in the
same report, with source paths and cross-container matches retained.

### Ambiguity is visible and can stop a benchmark run

`core_facts.issues` lists detected matching problems:

- A different complete assignment has the same optimal total weight and changes a
  selected record pair. A local tie alone is not enough.
- Two objects inside a record have indistinguishable recorded identities, such as
  two annealing steps with no sequence numbers and the same targets and materials.
- Both studies contain a record type but none of its owners pair. This may reflect
  an extraction error or unresolved naming; inspect it before interpreting zero
  contextual credit as failure to recover any information.

For fully ordered recipes, all longest order-preserving alignments are considered.
Only pairs shared by every optimal alignment earn contextual credit. If removing a
repeated operation makes its identity uncertain, affected steps are flagged. A
reversed order cannot be repaired by choosing matching temperatures or durations.
For partially ordered recipes, sequence, operation, targets and materials define
the scope; alternative operations may share a sequence number.

Conflicting values for the same scoped property in a layer and its sole linked
absorber also produce an issue. The scorer keeps both values visible rather than
selecting whichever agrees with the reference.

Affected facts stay in the counts but receive no attributed credit.
They can still receive recovery credit. An ambiguous parent record can also
block credit for facts in linked records. These rules deliberately withhold credit
when the recorded context does not distinguish a match; a reviewer must decide
whether the ambiguity reflects an extraction error or a scoring limitation.

`scoring_status="needs_review"` means the report contains such issues.
`"ready"` means only that these checks found none. A unique optimum under the
scorer's weights is not proof of a unique scientific identity.

Both scoring commands accept `--fail-on-scoring-issues`. They save the report, then
exit with an error if matching needs review. This option does not enforce a minimum
F1 or fail on citation issues. Do not omit flagged papers from the final results.
If the reference is wrong, correct it against the source and freeze a new version.
Do not invent sequence numbers just to remove a warning.

### Matching: which records refer to the same thing?

Pairing records and checking their values are separate steps. A record with a wrong
PCE can still describe the right device.

The scorer pairs families first, followed by individual devices, performance
observations, populations and stability tests. It pairs records only within the
same type. Parent pairings can then guide the child pairings.

Fully ordered families use `(2L+V+D)/(n+m+2)`: `L` is the longest common material
subsequence, `n,m` are layer counts, and `V` is one for exactly equal nonempty
variant labels after spacing normalization. `D` is Jaccard overlap of literal
family-label word sets, retaining internal hyphens and slashes and omitting words
shared by every family heading within a study. Missing/different
variants contribute zero; repeating label words adds no weight. Device candidates
likewise use literal label words alongside their other identity features. Labels
support alignment without inferring chemical synonyms or filling missing context.
Incomplete orders use the conservative Jaccard feature fallback (materials,
explicit positions, constituent names, absorber formulas and variant); missing
layers use the raw stack/architecture fallback. Recipes and outcomes cannot
establish family identity. See the [method specification](../methods/scientific-scoring.md)
for alignment and ambiguity rules.

When both devices provide treatment context, registered inputs are compared in
canonical units, together with stage, material and selection. Device properties
and structured conditions supply the same interpretation. Unknown conditions are
required by fact context but cannot break an identity tie. If no registered input
establishes identity, the literal variant is retained.

For other record types, similarity is the Jaccard overlap of declared identity
features, including a shared record-type feature:

| Record | Identity fields |
| --- | --- |
| Individual device | Label, variant, champion status, selection basis |
| Performance observation | Measurement type, scan direction, recognized explicit protocol conditions |
| Population | Cohort label, statistic type, recognized explicit protocol conditions |
| Stability test | Link status; specimen label without a device link; test conditions, checkpoint times and conditions |

Parent links guide assignment separately. Complete field phrases retain order, so
`10s/40s` and `40s/10s` remain distinct. Protocol quantities use canonical units
and the configured tolerance. Their complete set contributes one identity feature:
only conflicting shared conditions distinguish records; missing conditions do not.
Outcome values, outcome completeness and population sample sizes do not select
record identity. Omitting a metric cannot settle a repeated
protocol's ambiguity. Distinct records with indistinguishable identities remain
flagged even in a reference self-comparison.

A candidate must reach `--minimum-record-similarity`, which defaults to 0.35.
This number is a similarity threshold, not a probability of correctness.
Population candidates must also have matching cohort labels. Stability candidates
without explicit device links must have matching specimen labels. Outcomes cannot
override these identity checks. Explicit checked label equivalences, described below,
are applied before pairing and fact comparison.

An eligible pair receives 2 extra assignment points if its parent links agree
under the established pairings and its protocol fields agree. The protocol fields
are measurement type, scan direction and statistic type, where present.
Both-missing links can count as agreement. Families have no parent-link bonus.

The Hungarian algorithm chooses the one-to-one assignment with the largest total
weight. It does not choose each row's best candidate independently or maximize the
number of pairs as a separate objective. For example, with these eligible scores
and no differing parent bonuses:

| | Prediction A | Prediction B |
| --- | ---: | ---: |
| Reference 1 | 0.90 | 0.80 |
| Reference 2 | 0.85 | 0.40 |

Choosing A for reference 1 leaves B for reference 2, totaling 1.30.
Choosing 1→B and 2→A totals 1.65, so the scorer selects that assignment.
The report's `matches[].similarity` shows the content score without the bonus.

Attributed scores require paired owners and a compatible semantic concept.
Recovery matches compatible concepts without the owner requirement, including
across individual/population containers. Report groups and subfields are not
candidate matching keys. A comparison either passes or fails; there is no partial credit for
a value that falls outside the numeric tolerance. Layer and operation order comes
from explicit sequence fields, not array position.

Protocol values still help identify stability tests; their outcome values do not.
Similar specimens can be confused, and a wrong family pairing can affect its
children. To detect equally optimal assignments, the scorer examines the Hungarian
solver's reduced costs: edges with zero reduced cost can participate in another
optimum. A cycle that exchanges selected edges marks the affected pairs as ambiguous.
The zero test allows `1e-12 / N` for floating-point error, where `N` is the padded
matrix size; dummy rows and columns are included. This is a numerical tie check,
not a measure of scientific confidence. Inspect the reported pairs and field paths
when a score is surprising.

For example, weights `[[0.875, 0.75], [0.625, 0.5]]` have no local ties but two
complete assignments totaling 1.375: both pairs are ambiguous. Conversely,
`[[0.9, 0.9], [0.85, 0.4]]` has a local tie but a unique optimum of 1.75.

### Numeric tolerances: when are two values equal?

For eligible numeric `ReportedValue` entries, the scorer converts compatible
explicit units to base units and applies Python's `math.isclose` rule:

```text
abs(reference − prediction)
    <= max(relative_tolerance × max(abs(reference), abs(prediction)),
           absolute_tolerance)
```

| Setting | Default | Meaning |
| --- | ---: | --- |
| `--numeric-relative-tolerance` | `0.01` | 1% allowance relative to the larger absolute value |
| `--numeric-absolute-tolerance` | `0` | No absolute allowance by default |
| `--minimum-record-similarity` | `0.35` | Record-pairing threshold; not a numeric tolerance |

The 1% default allows modest numerical and rounding differences. It is a scoring
policy, not experimental uncertainty or an inference from significant figures.
It applies after unit conversion to numeric reported values in both facts and
context. Counts such as `sample_size` still require exact equality. Sequence
numbers establish structural order under the layer/process matching rules;
numeric tolerance never makes different orderings equivalent.

The examples assume that property and context also match:

| Reference | Prediction | Result |
| --- | --- | --- |
| PCE 20% | PCE 0.20, explicitly dimensionless | Equal after conversion |
| PCE 20% | PCE 20.00001% | Equal within tolerance |
| PCE 20.0% | PCE 20.04% | Equal within 1% |
| PCE 20% | PCE 20.20% | Equal within 1% |
| PCE 20% | PCE 20.21% | Different |
| Time 1 hour | Time 3600 seconds | Equal after conversion |
| Temperature 65 °C | Temperature 338.15 K | Equal after conversion |
| PCE 20% | PCE 20 with no unit | Different |
| PCE 20 percent | PCE 20 pct | Equal when raw suffix and unit agree |
| Value with an empty unit | Explicit dimensionless value | Different; an empty unit is missing |
| PCE >20% | PCE 20% | Different; a bound is not an exact value |

Near 20% PCE, the relative allowance is about **0.2 percentage points**.
Relative temperature comparisons use kelvin: near 65 °C, 1%
corresponds to approximately 3.4 K. The same rule applies to numeric protocol
conditions, so a 100 h versus 101 h checkpoint can match; 100 h versus 102 h cannot.

For a strict sensitivity analysis, use `--numeric-relative-tolerance 1e-6`.
Each report stores the exact tolerance in `config`; aggregates reject mixed
settings. The former default was `1e-6`. Rescore every compared arm under the same
chosen setting, using fresh output filenames. The matcher, schema and fact
inventory have not changed in this default-setting update.

The default absolute tolerance is zero: 1 nm and 2 nm must not match merely because
both are small in metres. An explicit absolute tolerance is still available, in base
units, but should only be used for a declared scope where that allowance is justified.
For example, `1e-9` would allow a whole nanometre of difference for thickness.
Converting first keeps the comparison consistent across unit representations.

For a single number, conversion requires both raw values to contain one unqualified number, with either
no suffix or a suffix equivalent to the stated unit at the same scale and offset.
For example, raw `1 h` with unit `hour`, or raw `65 ° C` with unit `°C`, can be
converted. Raw `1 h` with unit `s` cannot: its number and declared unit contradict
the raw text. The suffix check recognizes unit spelling, not a different magnitude.
Each parsed number must agree with
its raw number within an internal relative tolerance of `1e-9`, with no absolute
allowance. This prevents a small nonzero raw number from being accepted as a parsed
zero. That consistency check is separate from the scoring tolerance.

For plain scalar claims, an explicit recognized unit in raw text is sufficient:
`125 nm` equals parsed `0.125 µm` even if its separate `value_number` and `unit`
fields are null. The scorer fills only missing convenience fields internally;
supplied contradictory fields remain mismatches. It never changes the saved
prediction. A unit absent from both raw text and the unit field stays missing.

Printed powers of ten such as `2 × 10⁻⁶ mbar`, `2 × 10^{-6} mbar` and the
PDF-text form `2 × 10 -6 mbar` agree with `2e-6 mbar`. The complete claim must
still be an unqualified scalar, range or symmetric uncertainty. A changed exponent,
inequality or contradictory parsed number is not repaired.
Explicit sheet-resistance spellings such as `Ω sq−1`, `Ω/□` and `ohms per square`
are recognized; `Ω/m²` retains its different dimension. The property name must
still agree. See the [Keithley measurement guide](https://download.tek.com/document/Resistivity%20Mesrmnts%20using%202450%20AppNote.pdf)
for the ohms-per-square convention.

If both units are absent from the entire claim, eligible numbers can match without conversion. If only
one unit is missing, they cannot. If a unit is unrecognized, the scorer requires
equal normalized raw text and identical unit strings rather than guessing a conversion.

Lists of conditions, target layers and processing materials are compared without
using list order, but repeated entries still count. Each entry must find its own
match under the equality rules. Explicit layer and operation sequence remains
meaningful.

### Chemicals, ranges and free text

The scorer compares a range's two endpoints, or an uncertainty's centre and spread.
It never substitutes either claim for a single number. It accepts two finite numbers
joined by `–`, `—`, `-` or `to` for ranges, and `±` or `+/-` for symmetric uncertainty.
An optional trailing unit must agree with a supplied separate unit field at the same
scale and offset; a missing unit field can use the explicit trailing unit. A range
must have ordered endpoints and `value_number: null`. For an
uncertainty, the spread must be nonnegative; a supplied `value_number` must agree
with the centre. The same scoring tolerances apply separately to both components.

For example, `1–2 h` equals `60–120 min`, and `20 ± 1 %` equals
`0.20 ± 0.01 dimensionless`. An uncertainty of `20 ± 1 °C` equals
`293.15 ± 1 K`: the centre shifts, but the spread does not. A range is not
interchangeable with a mean plus an uncertainty, even when their endpoints coincide.
Inequalities, asymmetric errors, extra qualifiers such as `SD` or `SEM`, and
unrecognized units retain literal comparison. These rules do not infer what an
unlabelled error bar means.

Text comparison preserves chemical charge superscripts before Unicode normalization,
standardizes the minus sign and collapses whitespace. It does not turn `Fe³⁺` into
`Fe3+` (three Fe atoms with charge notation) or conflate `Co` with `CO`. Simple formulas also accept numeric LaTeX subscripts:
`CH_{3}NH_{3}PbI_{3}` equals `CH3NH3PbI3`. This rule requires the entire string to
consist of element-like tokens with numeric or bounded symbolic coefficients:
`I_{3-x}` equals `I3-x`, but neither equals `I3+x` or `I3-y`. This changes notation
without evaluating the expression. Charges, parentheses and arbitrary prose are
not simplified.

Material fields use the small explicit vocabulary in `scoring_vocabulary.py`:
for example, `silver`, `Ag` and `Silver (Ag)` agree. Both halves of a parenthetical
name must resolve to the same material; `Gold (Ag)` does not match gold.
Morphology, doping and mixtures are not stripped. Formula notation also accepts
spaces before numeric subscripts, such as `TiO 2`.
Known layer properties can omit a redundant qualifier naming their own material:
`silver contact thickness` and `thickness` agree on a silver layer. A qualifier
naming a different material does not. Unknown chemical names and property
paraphrases remain literal; this vocabulary is not general semantic understanding.

For example, `CoO` does not equal `COO`. The scorer does not infer that `MAPbI3`
and `CH3NH3PbI3` describe the same composition. Equivalent names or differently
written claims outside the supported grammar can therefore disagree. Matching text also does not prove that
either claim is supported by the paper.

Property names ignore case, whitespace, hyphens and underscores. Explicit aliases
map the full names of power conversion efficiency, open-circuit voltage,
short-circuit current density and fill factor to PCE, Voc, Jsc and FF.
`PCE retention` and `reference efficiency retained` map to `efficiency retention`;
absolute efficiency and retained efficiency remain different properties.

Operation names ignore case and normalize whitespace. By default, the one operation
alias maps `thermal annealing` to `annealing`. Use
`--operation-aliases aliases.json` to replace this map; supply a JSON file containing
`{}` to disable the aliases. Other paraphrases are not inferred.

Operation aliases affect fact comparison and the context of dependent conditions,
not whole-record token similarity. Keep the map fixed for an evaluation; the report
stores it with the other settings.

### Checked cohort names

Population labels and unlinked stability-specimen labels carry identity. The scorer
does not guess that similar names denote the same group. In particular, it does not
strip text after a semicolon: `control; with additive` is not `control`.

When two names have been checked against the source and denote the same group,
save that decision in a JSON list and pass `--descriptor-equivalences rules.json`.
Each entry records `reference_sha256`, `record_kind`, `reference_label`,
`alternative_labels`, and `reason`. Use the score report's `truth_content_sha256`
for `reference_sha256`, rather than the ZIP, manifest or file hash.

The scorer applies the rule only to that exact reference and record type. It rejects
overlapping rules and rules that would merge distinct reference groups. It changes
neither input file; reported hashes still identify the originals. The full rules
are saved under `config.descriptor_equivalences` in each score report.

[`examples/scoring/reviewed-descriptors-v2.json`](https://github.com/lamalab-org/perla-extract/blob/main/examples/scoring/reviewed-descriptors-v2.json)
contains the two label clarifications checked during the v2 audit. It is optional,
not a rule built into the scorer. Freeze any such list before comparing model
runs, apply it to every arm, and report the unadjusted scores too. If the reference
changes, its old rules no longer apply and must be checked again. Do not add rules
merely because they improve one model's score.

### LLM judging

There is no LLM judge in this scorer. It does not call a model to recognize chemical
synonyms, resolve ambiguous pairings or rewrite predictions. Experts must resolve
disagreements that the fixed comparison rules cannot settle.

### Diagnostic scores

The report also includes:

| Result | What it measures |
| --- | --- |
| `inventory` | Precision, recall and F1 for record counts in each of the five record types |
| `matches` | Selected record pairs, their similarity and detected ties |
| Scalar-field agreement | Agreement on non-quantity fields within paired records |
| Parent-link agreement | Whether paired records point to corresponding parents |
| `field_agreement.reported_values` | Recovery of named quantities, not correctness of their values |
| `reported_value_accuracy` within `field_agreement` | Value agreement among those paired quantities |
| Unmatched record keys | Reference and prediction records without a pair |

The older quantity diagnostic pairs values within each paired record using 80%
property-name agreement and 20% raw-text token similarity, with a 0.5 threshold.
It checks value equality afterward. Its quantity-recovery F1 can be perfect while
every value is wrong. It does not award the primary `core_facts` score.

These diagnostics answer narrower questions than scientific correctness. In
particular, scalar-field comparison simplifies text, and quantity recovery does not
check all nested associations. Use them to explain a result, not replace the
correct-value-and-context score.

<span id="reviewer-uncertainty"></span>

## Finalize the reference before scoring

The review workbench's **Benchmark reference coverage** section records text/table,
figure and attribution-context completeness for each scientific domain, with a
source-based rationale. These declarations travel through the export manifest and
`benchmark.coverage`. They default to unreviewed for existing exports.

`interpretation="reference_agreement"` means precision/F1 describe agreement with
the supplied annotations, and recall covers only that annotated subset. They are
not verified extraction-quality estimates. No prediction is silently excluded.
`interpretation="reviewed_scope"` requires complete or explicitly not-applicable
coverage for every domain/channel and attribution context. An unreviewed figure or
unknown scope keeps the overall benchmark diagnostic; comparison retains its counts
but withholds a verified quality conclusion. Coverage declarations record review;
they do not authenticate it. Figure-derived quantities require reviewed extraction
or digitization and a declared uncertainty policy.


The benchmark uses one fixed reference for every compared system. The scorer does
not exclude reference records or search for predictions to ignore.

Reviewers can save `uncertain` decisions while working. Before final adjudication,
an authorized finalizer must resolve every current record and mark it verified. Export
refuses unresolved records, and scoring also refuses older manifests that contain
them. Saved review history is not deleted or automatically reclassified.

Resolve a wrong value by correcting it against the paper. Remove an unsupported
claim rather than approving it. A reported bound, range or explicitly unknown
relationship can itself be verified when that is what the source says; verification
does not require inventing an exact value or device link.

If the source cannot support a decision within the intended review scope, keep
that paper pending and report it outside the finalized benchmark. Do not delete
difficult facts just to pass the export check. Record scope and coverage before
comparing systems.

The report records `reference_policy="fixed-no-exclusions"`. Bare JSON inputs and
direct Python calls remain useful for development, but cannot prove that review
took place. The evaluator has no per-record exclusion argument.

Matching problems are a separate issue: `core_facts.issues` reports them without
changing which reference facts are counted. A finalized reference can still expose
a limitation in the matcher.

## Dataset reporting

After inspecting per-paper results, combine their saved reports:

```bash
perla-evaluate-dataset \
  --report results/paper-a/evaluation.json \
  --report results/paper-b/evaluation.json \
  --output results/model-x.dataset-evaluation.json
```

For the published benchmark, also pass
`--release data/study_extraction/ground_truth/v2/release.json` and provide one
`--report` for every paper in that release. The two-paper example above is only
for a separate small dataset; it will correctly fail against the 30-paper release.

The command does not rerun matching. It rejects incompatible report formats, schema
hashes, matcher versions and scoring settings. It also rejects a mix of reports with
and without reference manifests.

For reports with reference manifests, it checks that splits agree and rejects
duplicate paper IDs or overlapping source hashes. If source hashes are absent, it
uses the source-manifest hash to detect duplicates. Bare-JSON reports lack these
paper-identity checks.

With `--release`, the command rejects missing or extra papers, duplicates, different
reference hashes and different splits before writing an aggregate. Without it,
the command cannot tell whether you left out a paper. A failed run is not permission
to shrink the benchmark: retain the failure and resolve how to score it before
reporting a complete result. The command does not synthesize predictions for failures
or enforce one extraction model or configuration; check those run settings yourself.

Every aggregate retains `input_reports`, with each paper's reference provenance,
reference-content hash, prediction-content hash and report hash. When a release is
supplied, `reference_release_sha256` also identifies its full parsed metadata.
Hashes use sorted, compact UTF-8 JSON rather than the original file formatting;
the release digest includes a final newline, and the report digest does not.
Keep the input reports and release file with the aggregate so these hashes can be
checked. This is a reproducibility record, not a cryptographic signature.

### Totals and paper averages

The report keeps both pooled counts and paper averages:

- `core_fact_groups_micro` pools fact counts for each scientific group.
- `core_fact_groups_macro_f1` averages each group's F1 across papers.
- `core_facts_micro` pools facts across all groups and papers.
- `core_facts_mean_paper_f1` averages each paper's attributed micro F1.

The last measure gives each paper with a defined score equal weight, rather than
letting a paper with many fields dominate. Undefined rates are left out, and each
average records how many papers contributed. Inventory, field and relationship
diagnostics retain their own counts and paper averages.

The recovery view remains available as `core_recovery_micro` and
`core_recovery_mean_paper_f1`. Check `papers_with_unreviewed_scope`,
`papers_needing_scoring_review` and
`scoring_issue_count` alongside all scores.

### Intervals

By default, the command draws 2,000 bootstrap samples with seed 0. Each
sample resamples the contributing paper scores with replacement and calculates their
mean. The reported 95% bounds come from the sorted sample means. These are estimates
of uncertainty in a single system's mean, not a paired significance test of two systems.

Bounds are `null` if no paper has a defined rate, only one paper contributes, or
bootstrapping is disabled. `interval_status` explains which case applies.
The report stores `bootstrap_samples`, `bootstrap_seed` and `bootstrap_method`.
Fixed inputs, report order and settings reproduce the bootstrap results; an interval
does not guarantee performance on unseen papers.

### Cost and validation coverage

Dataset `efficiency.cost_usd` sums recorded costs. Treat it as a complete total only
when `cost_tracking_complete` is true. The fields `cost_complete_papers`,
`cost_incomplete_papers` and `cost_unknown_papers` show coverage; missing accounting
counts as unknown, not free.

The dataset report also counts papers with prediction-validation results, papers
that passed those automated checks and remaining validation issues. Passing those
checks still does not prove scientific correctness.

Keep calibration, development and test results separate. Papers used to change
parsing, prompts, schemas, matching, thresholds or model selection are not held out.
When a reference changes, freeze a new version and rescore all compared predictions
against it.


The [scientific scoring methods](../methods/scientific-scoring.md) give the exact
identity, layer-alignment, context and numeric rules for a paper methods section.
Explanation format 5 names context as equivalent, contradictory or unresolved;
that classification does not turn unresolved facts into matches. The study schema
and ground-truth format have not changed.
