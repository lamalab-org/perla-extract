# Scoring reference

`perla-evaluate` compares a rich `extraction.json` with one frozen, adjudicated
`StudyExtraction`. It is deterministic and never calls an LLM. Run-local record IDs
and evidence quotations are excluded from record similarity; evidence validity is a
separate extraction-validation result.

For the scientific protocol and evidence status, read [Evaluation methods](../methods/benchmark.md).
For commands using saved expert corrections, read [From corrections to a benchmark](review-to-benchmark.md).
The [worked example](scoring-example.md) runs without PDFs or an API key.

The scorer measures agreement with an adjudicated reference within its declared scope.
It does not certify the completeness of that reference or the correctness of record
alignment. Source review and scorer validation are separate requirements.

The following paths illustrate a real-study run; they are not bundled benchmark data:

```bash
perla-evaluate \
  --truth data/study_extraction/ground_truth/v1/dev/10.1126--science.adf0194 \
  --prediction results/10.1126--science.adf0194 \
  --output results/10.1126--science.adf0194/evaluation.json
```

When `--truth` is a frozen benchmark directory, the command verifies the schema hash
and canonical `ground_truth.json` content hash before scoring. The resulting report
also records the paper ID, split, truth hash, source-document hashes, and a fallback
source-manifest hash. Passing a bare truth JSON is useful for development but does not
provide those provenance checks.

Hash checks bind a score to declared content; they do not authenticate expert
judgments. The evaluator does not repeat the workbench's adjudication gates or
revalidate the archived reference evidence. Use the normal review/export workflow
and inspect release provenance, rather than constructing a manifest to certify a seed.

Frozen ground-truth formats 2 and 3 are supported. Format 3 additionally requires and
records the reviewed evidence version and document hash. Evaluation reports now use
format **4** and matcher **rich-study-hungarian-v4**; regenerate older score reports
from their saved predictions instead of mixing old and new scores.
Every report also hashes both parsed study inputs, including IDs and array order,
so its diagnostic JSON paths can be tied to the inputs that produced it. These
normalized-content hashes are not hashes of the original file bytes.

Prefer a complete extraction run directory for `--prediction`. The command then
recomputes evidence and relationship validation from `extraction.json` and
`document.json` and embeds the result in the score report. It also validates and
retains measured calls, tokens, reported cost, and elapsed time from `report.json`.
A bare prediction JSON is accepted for development, but its report marks validation
and efficiency accounting as unavailable.

## What is scored

### Primary score: correct scientific facts

Use `core_facts`, not the older quantity-presence score, to judge extraction quality.
The versioned `core-scientific-facts-v3` profile covers:

| Group | Scientific fields counted | Context required for credit |
| --- | --- | --- |
| Performance | Reported performance metrics, including PCE, Voc, Jsc, FF | Matched device/family, variant, champion status, measurement type and scan direction |
| Population | Sample size and aggregate metrics | Matched family, statistic type and sample size |
| Stability | Test conditions, checkpoint times/conditions and outcomes | Matched specimen links; outcomes also require the correct time and test/checkpoint conditions |
| Composition | Absorber formula, constituents, their roles/amounts, absorber properties | Corresponding absorber/layer and chemical constituent |
| Stack | Layer materials, roles, physical form, properties and device polarity | Explicit layer sequence and material association; raw stack text is a fallback when structured layers are absent |
| Processing | Operations, materials, conditions and specimen-specific properties | Correct operation, explicit sequence, target layers, materials and device variant |

This selects fields by schema structure, not paper-specific keywords. Other property
names within these groups also score. IDs, citation wording, paper titles, display
labels, unresolved notes, and empty/`not_reported` scalar placeholders do not earn
points. Champion flags constrain performance context rather than earning redundant
points themselves. Extra scientific claims do count against precision.

A correct match requires the **quantity, value, units and scientific context** to
agree. A wrong PCE contributes one false positive and one false negative—not a true
positive merely because both records mention PCE. Repeated identical predictions
cannot reuse one truth fact. Every credited pair and every unmatched field is listed
by its original JSON path in `core_facts`.

For example, if truth reports PCE = 20% and the prediction reports 21% on the same
device, the performance score has `truth=1`, `predicted=1`, `matched=0`, and F1 = 0.
If truth reports annealing at 100 °C followed by 150 °C, reversing those temperatures
loses both condition matches even though the same two numbers are still present.

- `core_facts.groups`: precision, recall, F1 and counts for each scientific area.
- `core_facts.micro`: pooled correct-fact counts and rates.
- `core_facts.macro_f1`: equal-weight mean F1 over groups with truth or prediction
  facts. An entirely missing populated group scores zero; a group absent on both
  sides is undefined and excluded. Always show group counts alongside this headline.

### Separate value recovery from attribution

Each report now contains three comparisons with the **same denominators**:

| Report location | Question | Use |
| --- | --- | --- |
| `core_facts.value_only` | Was this property value recovered within its provisionally paired record, ignoring nested context? | Diagnose value loss; not a correctness headline |
| `core_facts.attribution` | Is this property present with the correct recorded context, regardless of its value? | Diagnose misplaced or insufficiently described measurements |
| `core_facts` | Are both the value and its context correct, with no detected attribution ambiguity? | Primary scientific score |

The three views use independent one-to-one assignments. Do not multiply their F1s
or intersect their path lists to reconstruct the strict score. A value-only match
does not establish specimen identity or chemical attribution.

For example, a stability test with one temperature, two times and two outcomes has
five facts. If a prediction omits the temperature but retains the other four facts,
value-only recall is 4/5 and F1 is 8/9. Strict credit can be zero because the outcomes
and times no longer have the complete test context. This exposes the missing
condition without suggesting the model failed to read all the numbers.

### Ambiguity is visible and can stop a benchmark run

`core_facts.issues` identifies competing record matches and nested objects that
cannot be distinguished by their recorded identities. Examples include two
annealing steps without sequence numbers, or two constituents with the same name,
amount type and scope. Outcome values cannot resolve these nested identities.

Affected facts remain in both denominators, but receive no strict or attribution
credit. The value-only view remains available for diagnosis. Thus an exact duplicate
observation can earn one value-only match but no strict credit while its pairing
is unresolved. This is deliberately conservative; it is not a final scientific
judgment about that duplication. Ambiguous record references also block attribution
credit for linked child facts.

`scoring_status="needs_review"` means inspect these issues before interpreting a
headline. `"ready"` means **no detected matching ambiguity**, not verified ground
truth, complete source coverage, or a proven unique scientific pairing. Record
warnings currently detect equal-scoring alternatives in the selected match's row
or column, not every possible alternative global assignment. They can be
conservative; absence of a warning is not proof of identity.

Both CLIs support `--fail-on-scoring-issues`: they write the complete report first,
then exit nonzero if matching needs review. Do not drop these papers from a dataset
to obtain a clean score. Resolve reference issues with source evidence and a new
truth version; retain genuine prediction errors. Never fabricate sequence numbers
solely to satisfy the scorer.

### Matching: which records refer to the same thing?

Matching and correctness are different decisions. A prediction with the wrong PCE
can still describe the right device; pairing those records lets us identify the
value error rather than calling the entire device missing.

The implemented matcher works in two levels:

1. **Pair whole records**, separately for families, individual devices, performance
   observations, population statistics and stability tests, in that order.
2. **Compare facts inside those pairings**, separately for each scientific group
   and property name. One prediction fact cannot satisfy several reference facts.

For whole records, the content-similarity formula is:

```text
content similarity = 0.75 × Jaccard(record-content tokens)
                   + 0.25 × Jaccard(canonical reported-property names)

Jaccard(A, B) = number of shared entries / number of distinct entries in either set
```

Record-content tokens include descriptions and materials. Eligible numeric claims
use canonical base-unit values and units, and metric names use the same explicit
aliases as fact scoring; other claims retain raw values and units. Canonical numbers
use 12 significant digits for lexical features only, never for the final comparison.
IDs and citations are excluded; parsed numbers are used only after their consistency
with the raw value has been checked. Text is
lowercased and punctuation simplified for this **candidate-matching step only**;
the later chemical-value comparison preserves case and punctuation. Two empty
token sets have similarity 1 by convention, not because they establish identity.

A candidate must reach `--minimum-record-similarity`, default **0.35**. This is a
heuristic similarity threshold, not a 35% confidence estimate. A surviving pair
receives an additional **2** in its assignment weight when its parent relationships
agree under already established pairings and its protocol fields agree. Protocol
fields are measurement type, scan direction, statistic type and sample size where
present. Both-missing parent fields can count as agreement; the bonus does not prove
that a link was reported. Families have no parent-link bonus.

The Hungarian algorithm selects a **maximum-total-weight, one-to-one assignment**
over all candidates of that record type. It does not choose each row's favourite
independently, and it does not optimize the number of matched records separately
from their weights. For example, with these illustrative eligible scores and no
differing parent bonuses:

| | Prediction A | Prediction B |
| --- | ---: | ---: |
| Reference 1 | 0.90 | 0.80 |
| Reference 2 | 0.85 | 0.40 |

Greedily assigning reference 1 to A would leave B for reference 2, totaling 1.30.
The global assignment instead uses 1→B and 2→A, totaling 1.65. This avoids a common
order-dependent matching error. Unmatched reference records reduce inventory recall;
unmatched predictions reduce inventory precision. Reported `matches[].similarity`
is the **unboosted** content similarity.

After pairing, facts compete only within the same scientific group, paired owner
and property name. Fact comparisons are binary: they either satisfy the selected
equality/context rule or they do not. There is no partial credit because two PCEs
are “fairly close” beyond the numeric tolerance. Layer and processing order comes
from explicit sequence fields, not JSON array position.

**Limitations to inspect:** values influence whole-record alignment, so this is
not an outcome-blind identity matcher. Similar specimens can still be confused,
and a badly paired family can affect its linked records. Equal-weight alternatives
in a selected pair's row or column are flagged using an internal absolute score
tolerance of `1e-12`; this does not find every possible alternative global optimum.
Repeated nested objects with indistinguishable recorded identities are also flagged.
Inspect `matches`, `core_facts.issues` and the original paths before accepting a
headline. A deterministic assignment is not proof of scientific identity.

The older `field_agreement.reported_values` diagnostic uses a different quantity
matcher: 80% canonical property-name agreement plus 20% raw-text token similarity,
with a 0.5 threshold, followed by a value comparison. It is retained for diagnosis,
not used to award the primary `core_facts` score.


### Numeric tolerances: when are two values equal?

For two eligible scalar `ReportedValue` entries, first convert compatible explicit
units to canonical base units on both sides, then apply Python's `math.isclose` rule:

```text
abs(reference − prediction)
    <= max(relative_tolerance × max(abs(reference), abs(prediction)),
           absolute_tolerance)
```

| Setting | Default | Meaning |
| --- | ---: | --- |
| `--numeric-relative-tolerance` | `1e-6` | Allow a difference proportional to the larger absolute value |
| `--numeric-absolute-tolerance` | `1e-9` | Allow a small difference near zero, in canonical base units |
| `--minimum-record-similarity` | `0.35` | Whole-record candidate threshold; unrelated to numeric accuracy |

These tolerances accommodate conversion/floating-point precision. They are **not**
experimental error bars, significant-figure inference, or permission to round an
extracted measurement freely. They apply to numeric reported values, including
numeric test context. Ordinary schema integers such as sample size and sequence
are compared exactly, not approximately.

Examples, assuming the property and scientific context also agree:

| Reference | Prediction | Result with defaults |
| --- | --- | --- |
| PCE 20% | PCE 0.20, explicitly dimensionless | Equal after conversion |
| PCE 20% | PCE 20.00001% | Equal within tolerance |
| PCE 20% | PCE 20.0001% | Different |
| PCE 20.0% | PCE 20.04% | Different; no automatic rounding-to-reported-precision rule |
| Time 1 hour | Time 3600 seconds | Equal after conversion |
| Temperature 65 °C | Temperature 338.15 K | Equal after conversion |
| PCE 20% | PCE 20 with no unit | Different; missing is not an explicit percent unit |
| PCE >20% | PCE 20% | Different; an inequality is not an exact value |

At PCE 20%, the relative allowance is approximately **0.00002 percentage points**,
not one percentage point. The absolute allowance is in base units: seconds for
time, Kelvin for temperature and a dimensionless fraction for percent. This prevents
the tolerance from changing when the same reference is expressed in another unit,
including an offset temperature scale. Freeze the scoring version and tolerances;
version-4 reports must not be mixed with older reference-unit-based scores.

Unordered context lists are compared as multisets with one-to-one matching under
these same equality rules. This preserves duplicates and handles whitespace/unit
changes without an artificial disagreement caused by sorting raw text. Explicit
layer and operation sequence is not treated as unordered.

Unit conversion is attempted only when each raw value is a single, unqualified
number, with no suffix or a suffix matching its stated unit. The parsed number
must agree with that raw number within an internal consistency check (`1e-9`
relative, `1e-12` absolute). That check is distinct from the scoring tolerance.
If both units are missing, eligible numbers can be compared without conversion;
the scorer does not infer what the missing unit was. Unrecognized units fall back
to conservative literal equality, not guessed conversion.

### Chemicals, ranges and free text

Ranges, uncertainties, inequalities and formulas do not become equivalent merely
because their parsed central number agrees. They fall back to literal comparisons
of normalized raw text, unit and parsed number. Unicode typography and repeated
whitespace are normalized; chemical case, punctuation and stoichiometry remain
significant. Equality of two representations does not itself validate either claim
against the paper.

Consequently, `CoO` is not `COO`, and the scorer does not infer that `MAPbI3` and
`CH3NH3PbI3` represent the same composition. Equivalent differently written ranges
or chemical names can produce conservative false disagreements. Inspect these on
development papers instead of hiding them in a generous universal tolerance.

Property names have explicit aliases for PCE, Voc, Jsc and FF. Operation names use
an explicit, frozen map: by default, `thermal annealing` maps to `annealing`.
Supply `--operation-aliases aliases.json` to replace the map, or `{}` to disable it.
This applies to fact comparison, not the lexical whole-record matching formula.
Other operation paraphrases are not inferred. Changing aliases can affect both
operation matches and their dependent condition matches; freeze the map before
held-out evaluation and retain it in each report.


### LLM judging

The implemented scorer makes no LLM or embedding calls. Experts resolve semantic
disagreements; the scorer does not infer chemical synonyms or rewrite predictions.
A future judge-assisted analysis would require a separately validated, versioned
protocol and must not be mixed with these deterministic scores.

### Diagnostic scores

The report keeps distinct questions separate:

- inventory precision, recall, and F1 for families, devices, observations,
  populations, and stability tests;
- the exact record pairings selected by a global one-to-one matcher;
- scalar-field agreement on matched records;
- parent-link agreement on matched records, such as whether an observation points to
  the matched device;
- end-to-end atomic `ReportedValue` **presence** precision/recall across scored records and
  conditional value agreement for matched quantities, including compatible unit
  conversion; and
- unmatched truth and prediction record keys for error analysis.

`field_agreement.reported_values` is retained for diagnosis: it can be perfect while
the values themselves are wrong. `core_facts` is the stricter correct-in-context
score. Citation validation is separate and does not prove that the quoted source
supports the claim scientifically.

Rates with a zero denominator are `null`, not a vacuous perfect score. Always retain
the predicted, truth, and matched counts when aggregating reports.

## Reviewer uncertainty

A record marked `uncertain` at final adjudication is not a positive or negative label.
The format-3 ground-truth manifest stores those record keys as an abstention mask and
binds the truth revision to the evidence-document version used during review.
Certain truth records are matched first; a remaining prediction that matches an
uncertain record is excluded from both precision and recall. The report lists every
masked prediction so abstention cannot silently improve a score.

## Dataset reporting

After inspecting the per-paper pairings, aggregate their immutable reports without
rerunning matching:

```bash
perla-evaluate-dataset \
  --report results/paper-a/evaluation.json \
  --report results/paper-b/evaluation.json \
  --output results/model-x.dataset-evaluation.json
```

The aggregator refuses reports with different schema hashes, matcher versions, or
threshold/tolerance/alias configurations. It also refuses to mix provenance-verified and
development reports, benchmark splits, duplicate paper IDs, or duplicate source
documents/manifests. It reports micro counts for records, fields, relationships, and
atomic values; macro paper-level rates; and deterministic 95% paper-bootstrap
intervals. It also totals how many predictions carried evidence validation, how many
were verified, how many validation issues remained, and all available run-efficiency
counts. Undefined paper-level rates are excluded with their contributing paper count
reported explicitly.

Interval bounds are `null` when bootstrap is disabled, no paper contributes a
defined rate, or only one paper contributes. `interval_status` distinguishes
`disabled`, `no_values`, `insufficient_papers` and `available`. The aggregate records
`bootstrap_samples`, `bootstrap_seed` and `bootstrap_method`; a finite bootstrap
interval is still an estimate, not a guarantee about unseen papers.

Dataset `efficiency.cost_usd` is the sum of **observed** costs. Interpret it as a
complete total only when `cost_tracking_complete` is true. The fields
`cost_complete_papers`, `cost_incomplete_papers` and `cost_unknown_papers` account
for every input report; missing run accounting counts as unknown. An unknown price
is not replaced with an invented estimate or described as a free call.

The dataset's `core_fact_groups_micro` and `core_fact_groups_macro_f1` report each
scientific area. `core_facts_micro` pools facts; `core_facts_macro_f1` averages each
paper's group-balanced score and bootstraps **papers**, not individual fields. This
avoids letting one unusually long supporting-information document dominate the
headline result. These are single-system intervals, not paired A/B significance tests.

`core_value_only_micro`, `core_value_only_macro_f1`, `core_attribution_micro` and
`core_attribution_macro_f1` retain the two diagnostic views. The aggregate also
reports `papers_needing_scoring_review` and `scoring_issue_count`; review them before
interpreting the strict headline. Per-paper reports retain all six groups and exact
matched/unmatched paths for each view.

Keep calibration, development, and test manifests separate. Papers used to change
parsing, prompts, schemas, matching, thresholds, or model selection are not held out.

## Comparing systems

Freeze the same paper roster, source scope, reference and scoring configuration for
both systems. The aggregator does not enforce a roster or implement a paired A/B
significance test. The [evaluation protocol](../methods/benchmark.md#comparison-studies)
distinguishes rich-schema accuracy, historical-database review, and preference.
