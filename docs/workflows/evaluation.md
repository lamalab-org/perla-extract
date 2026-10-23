# Scoring reference

`perla-evaluate` compares an extraction with a reference using fixed rules. It makes
no LLM or embedding calls. The result measures agreement with that reference—not
whether the paper was fully or correctly understood.

This guide uses three terms:

- **Reference:** the records you have reviewed against the paper.
- **Prediction:** the unchanged extraction you want to evaluate.
- **Fact:** one scored item, such as a PCE value, a layer material or an annealing
  temperature, together with the context needed to interpret it.

The scorer first pairs records, then compares their facts. It reports missing and
extra facts, wrong values and wrong associations separately. Record pairing is an
estimate: the scorer can pair two records incorrectly.

For the overall method, read [Evaluation method](../methods/benchmark.md).
For the steps from saved corrections to scores, read
[From corrections to a benchmark](review-to-benchmark.md).
The [worked example](scoring-example.md) runs without PDFs or an API key.

## Run a comparison

The paths below are examples; the repository does not include this paper's reference:

```bash
perla-evaluate \
  --truth data/study_extraction/ground_truth/v1/dev/10.1126--science.adf0194 \
  --prediction results/10.1126--science.adf0194 \
  --output results/10.1126--science.adf0194/evaluation.json
```

A frozen reference directory contains `ground_truth.json` and `manifest.json`.
The command checks that the schema hash matches the installed schema and that the
reference's content hash matches its manifest. It records the paper ID, split,
reference hash, source hashes and source-manifest hash in the report.

These checks detect mismatched files. They do not prove that anyone reviewed the
reference. The scorer does not repeat adjudication or check the reference's
citations against the paper. Use the [review and export workflow](ground-truth-review.md)
to create the reference.

New references use format 4 and require a final verified decision for every record.
Older formats 2 and 3 are accepted only if their unresolved-record list is empty.
Formats 3 and 4 must also declare the reviewed
evidence-document version and hash. The scorer records them; it does not load that
archived document to verify the declaration.

For `--prediction`, prefer a complete run directory containing:

| File | What the scorer does with it |
| --- | --- |
| `extraction.json` | Loads the predicted study |
| `document.json` | Rechecks the prediction's citations and record links |
| `report.json` | Checks the structure of the saved call, token, cost and timing totals, then copies them into the evaluation |

Citation checks do not determine whether a passage supports a claim scientifically.
Citation issues do not reduce the fact score automatically; inspect the validation
result alongside the score.
Run accounting comes from the saved report; the scorer does not audit provider bills.
You must also check that prediction and reference cover the same paper, SI and
scientific scope.

You can pass a bare JSON file for either study. Bare references have no manifest
checks or review-status checks; bare predictions have no evidence-validation
or run-accounting results.

Reports use format **5**, matcher **rich-study-hungarian-v5** and fact profile
**core-scientific-facts-v3**. Regenerate older reports from saved predictions before
combining scores. Each report hashes both parsed studies, including IDs and array
order, so you can locate the inputs behind its field paths. These are hashes of
normalized JSON content, not the original file bytes.

## What is scored

### Primary score: correct scientific facts

Use `core_facts` to assess extraction quality. The scorer counts facts in six groups:

| Group | What counts | What must agree for a match |
| --- | --- | --- |
| Performance | Reported metrics, such as PCE, Voc, Jsc and FF | Paired record, device/family links, variant, champion status, selection basis, measurement type and scan direction |
| Population | Sample size and aggregate metrics | Paired record, family link and statistic type; metric matches also require the same sample size |
| Stability | Test conditions, checkpoint times, checkpoint conditions and outcomes | Paired record and specimen links; checkpoint facts also depend on the recorded time and conditions, as described below |
| Composition | Absorber formula and properties; layer and absorber constituents, roles and amounts | Paired family, absorber/layer context and constituent association |
| Stack | Layer materials, roles, material forms and properties; device polarity | Paired family, recorded sequence and material association |
| Processing | Operations, materials, conditions and specimen-specific properties | Paired record, operation, recorded sequence, target layers and relevant materials or device variant |

These groups select parts of the schema, not a fixed list of paper-specific property
names. For example, a reported processing condition can count even if its name is
unfamiliar to the scorer. This is not an assessment of every field in the schema.

Some details affect how facts are counted:

- Structured layers take precedence over the raw stack string. If there are no
  layers, the scorer counts the raw stack string—or, failing that, architecture—as
  one fact. It does not split the string into layer facts.
- A layer material is compared at its recorded sequence. Its role, form and
  properties also require the material to agree.
- Processing conditions require the operation, sequence, target layers and material
  list to agree. Specimen-specific properties use the device's family and variant.
- Stability checkpoint times require matching test and checkpoint conditions.
  Checkpoint conditions require matching test conditions and time. Outcomes require
  matching test conditions, time and checkpoint conditions.

IDs, citations, titles, notes and display labels do not earn separate fact points.
Neither do absent, empty or `not_reported` scalar fields. Labels can still influence
record pairing; when several absorbers lack layer links, absorber labels also help
distinguish their context. Champion status constrains performance matches rather
than earning its own point.

For quantities, a match requires the property name, value, units and recorded
context to agree under the rules below. Non-numeric facts use the corresponding
text or exact-value rules. A match also requires no detected ambiguity affecting
that fact. Extra facts reduce precision; missing facts reduce recall.

For example, suppose the reference reports PCE = 20% and the prediction reports 21%
for the same paired device and scan. The counts are `truth=1`, `predicted=1` and
`matched=0`. The wrong value counts as both an extra predicted fact and a missing
reference fact. It does not earn credit for merely mentioning PCE.

One prediction fact cannot match several reference facts. Repeating a correct value
does not earn more credit. The report lists each matched pair and each unmatched
fact by its JSON path.

### Read precision, recall and F1

```text
precision = matched / predicted
recall    = matched / reference
F1        = 2 × matched / (predicted + reference)
```

A rate is `null` when its denominator is zero. For example, an empty prediction
against a nonempty reference has undefined precision, zero recall and zero F1.
If both sides are empty, all three rates are undefined.

- `core_facts.groups` gives counts and scores for each of the six groups.
- `core_facts.micro` pools facts across groups before calculating the scores.
- `core_facts.macro_f1` averages the F1 scores of groups with facts on either side.

A group with reference facts but no predicted facts scores zero. A group empty on
both sides is left out of the average. Equal group weighting is a reporting choice,
not a claim that every group has equal scientific importance. Show the group counts
alongside the average.

### Separate value recovery from attribution

The report compares the same facts in three ways:

| Report field | Question |
| --- | --- |
| `core_facts.value_only` | Within the paired record and property, does the value match, ignoring its nested context? |
| `core_facts.attribution` | Does the property have matching context, regardless of its value, with no detected ambiguity? |
| `core_facts` | Do both value and context match, with no detected ambiguity? |

All three use the same predicted and reference counts, but each finds its own
one-to-one fact matches. Do not multiply their scores or combine their match lists
to reconstruct the primary score. A value-only match does not prove that the value
belongs to the right specimen or chemical.

Consider a stability test with one temperature, two checkpoint times and two
outcomes: five facts in total. If the prediction omits the temperature but retains
the four other values, value-only recall is 4/5 and F1 is 8/9. The primary score can
be zero because the times and outcomes no longer have matching test conditions.
The separate views show that the numbers were recovered but their context was not.

### Ambiguity is visible and can stop a benchmark run

`core_facts.issues` lists detected matching problems. Two kinds are checked:

- A selected record pair has an equally weighted alternative in its row or column.
- Two objects inside a record have indistinguishable recorded identities, such as
  two annealing steps with no sequence numbers and the same targets and materials.

For these nested objects, the scorer does not use the outcomes being scored to
decide which object is which. For example, it will not identify an annealing step
solely by its temperature.

Affected facts stay in the counts but receive no primary or attribution credit.
They can still receive value-only credit. An ambiguous parent record can also
block credit for facts in linked records. These rules deliberately withhold credit
when the recorded context does not distinguish a match; a reviewer must decide
whether the ambiguity reflects an extraction error or a scoring limitation.

`scoring_status="needs_review"` means the report contains such issues.
`"ready"` means only that these checks found none. The checks do not prove a unique
scientific pairing and do not find every possible alternative assignment.

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

For each possible pair, it calculates:

```text
content similarity = 0.75 × Jaccard(record-content tokens)
                   + 0.25 × Jaccard(normalized property names)

Jaccard(A, B) = number of shared entries / number of distinct entries in either set
```

Record-content tokens include materials, descriptions and reported values. The
scorer excludes record IDs, link IDs and evidence quotations from these tokens.
For eligible numbers with recognized units, it uses base-unit values and units;
otherwise it uses raw values and units. It formats canonical numbers to 12 significant
digits for this pairing step only. Final numeric comparison does not use that rounding.

For token matching, the scorer lowercases text and simplifies punctuation.
For scientific value comparison, it preserves chemical case and punctuation.
Property names use the aliases described below. Two empty token sets have similarity
1 by convention; that is not evidence that the records describe the same object.

A candidate must reach `--minimum-record-similarity`, which defaults to **0.35**.
This number is a similarity threshold, not a probability of correctness.

An eligible pair receives **2** extra assignment points if its parent links agree
under the established pairings and its protocol fields agree. The protocol fields
are measurement type, scan direction, statistic type and sample size, where present.
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

The scorer then compares facts only within a paired record, scientific group and
property name. A comparison either passes or fails; there is no partial credit for
a value that falls outside the numeric tolerance. Layer and operation order comes
from explicit sequence fields, not array position.

Values influence record pairing, so this is not a value-independent identity check.
Similar specimens can be confused, and a wrong family pairing can affect its
children. Equal-weight alternatives are flagged within `1e-12` absolute difference,
but the check does not search all alternative global assignments. Inspect the
reported pairs and field paths when a score is surprising.

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
| `--numeric-relative-tolerance` | `1e-6` | Allowed difference relative to the larger absolute value |
| `--numeric-absolute-tolerance` | `0` | No absolute allowance by default |
| `--minimum-record-similarity` | `0.35` | Record-pairing threshold; not a numeric tolerance |

The relative tolerance allows small conversion and floating-point differences. It does not
represent experimental uncertainty or infer precision from significant figures.
They apply to numeric reported values in both facts and context. Schema integers,
such as sample size and sequence, must match exactly.

The examples assume that property and context also match:

| Reference | Prediction | Result |
| --- | --- | --- |
| PCE 20% | PCE 0.20, explicitly dimensionless | Equal after conversion |
| PCE 20% | PCE 20.00001% | Equal within tolerance |
| PCE 20% | PCE 20.0001% | Different |
| PCE 20.0% | PCE 20.04% | Different; no automatic rounding |
| Time 1 hour | Time 3600 seconds | Equal after conversion |
| Temperature 65 °C | Temperature 338.15 K | Equal after conversion |
| PCE 20% | PCE 20 with no unit | Different |
| PCE >20% | PCE 20% | Different; a bound is not an exact value |

At 20% PCE, the relative allowance is about **0.00002 percentage points**.
The default absolute tolerance is zero: 1 nm and 2 nm must not match merely because
both are small in metres. An explicit absolute tolerance is still available, in base
units, but should only be used for a declared scope where that allowance is justified.
For example, `1e-9` would allow a whole nanometre of difference for thickness.
Converting first keeps the comparison consistent across unit representations.

Conversion requires both raw values to contain one unqualified number, with either
no suffix or a suffix matching the stated unit. Each parsed number must agree with
its raw number within an internal relative tolerance of `1e-9`, with no absolute
allowance. This prevents a small nonzero raw number from being accepted as a parsed
zero. That consistency check is separate from the scoring tolerance.

If both units are missing, eligible numbers can match without conversion. If only
one unit is missing, they cannot. If a unit is unrecognized, the scorer requires
equal normalized raw text and identical unit strings rather than guessing a conversion.

Lists of conditions, target layers and processing materials are compared without
using list order, but repeated entries still count. Each entry must find its own
match under the equality rules. Explicit layer and operation sequence remains
meaningful.

### Chemicals, ranges and free text

The scorer does not reduce a range, inequality, uncertainty or formula to its parsed
number. It compares normalized raw text, unit and parsed number instead. It applies
Unicode NFKC normalization, standardizes the minus sign and collapses whitespace;
it does not ignore chemical case, other punctuation or stoichiometry.

For example, `CoO` does not equal `COO`. The scorer does not infer that `MAPbI3`
and `CH3NH3PbI3` describe the same composition. Equivalent names or differently
written ranges can therefore disagree. Matching text also does not prove that
either claim is supported by the paper.

Property names ignore case, whitespace, hyphens and underscores. Four explicit
aliases map the full names of power conversion efficiency, open-circuit voltage,
short-circuit current density and fill factor to PCE, Voc, Jsc and FF.

Operation names ignore case and normalize whitespace. By default, the one operation
alias maps `thermal annealing` to `annealing`. Use
`--operation-aliases aliases.json` to replace this map; supply a JSON file containing
`{}` to disable the aliases. Other paraphrases are not inferred.

Operation aliases affect fact comparison and the context of dependent conditions,
not whole-record token similarity. Keep the map fixed for an evaluation; the report
stores it with the other settings.

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

The benchmark uses one fixed reference for every compared system. The scorer does
not exclude reference records or search for predictions to ignore.

Reviewers can save `uncertain` decisions while working. Before final adjudication,
an administrator must resolve every current record and mark it verified. Export
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
took place. The legacy Python `ignored_truth_record_keys` argument now rejects any
nonempty list.

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

The command does not rerun matching. It rejects incompatible report formats, schema
hashes, matcher versions and scoring settings. It also rejects a mix of reports with
and without reference manifests.

For reports with reference manifests, it checks that splits agree and rejects
duplicate paper IDs or overlapping source hashes. If source hashes are absent, it
uses the source-manifest hash to detect duplicates. Bare-JSON reports lack these
paper-identity checks.

The command cannot tell whether you left out a paper. Keep a separate list of all
intended papers and account for missing and failed runs. It also does not enforce
one extraction model or configuration across reports; check those run settings yourself.

### Totals and paper averages

The report keeps both pooled counts and paper averages:

- `core_fact_groups_micro` pools fact counts for each scientific group.
- `core_fact_groups_macro_f1` averages each group's F1 across papers.
- `core_facts_micro` pools facts across all groups and papers.
- `core_facts_macro_f1` averages each paper's group-balanced F1.

The last measure gives each paper with a defined score equal weight, rather than
letting a paper with many fields dominate. Undefined rates are left out, and each
average records how many papers contributed. Inventory, field and relationship
diagnostics retain their own counts and paper averages.

The value-only and attribution views remain available as `core_value_only_micro`,
`core_value_only_macro_f1`, `core_attribution_micro` and
`core_attribution_macro_f1`. Check `papers_needing_scoring_review` and
`scoring_issue_count` alongside all scores.

### Intervals

By default, the command draws **2,000** bootstrap samples with seed **0**. Each
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
