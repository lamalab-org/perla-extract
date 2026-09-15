# Evaluate an extraction

`perla-evaluate` compares a rich `extraction.json` with one frozen, adjudicated
`StudyExtraction`. It is deterministic and never calls an LLM. Run-local record IDs
and evidence quotations are excluded from record similarity; evidence validity is a
separate extraction-validation result.

For the full review-to-score workflow, including reconciling older expert Excel
comments, start with [From reviewer corrections to a benchmark](review-to-benchmark.md).

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

Frozen ground-truth formats 2 and 3 are supported. Format 3 additionally requires and
records the reviewed evidence version and document hash. Evaluation reports now use
format **3** and matcher **rich-study-hungarian-v3**; regenerate older score reports
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
The versioned `core-scientific-facts-v2` profile covers:

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

### Equality rules and limitations

Single unqualified numbers with explicit compatible units are converted using Pint.
The default relative tolerance is `1e-6` and absolute tolerance `1e-9` in the truth's
unit—intended for conversion precision, not experimental uncertainty. These are
recorded in the report and configurable at the CLI. Missing units are not silently
treated as dimensionless. Explicit fractions and percentages are comparable in
both directions, as are Celsius and Kelvin.

Inequalities, ranges, uncertainties and formulas use conservative literal comparison,
including their raw qualifier. A normalized central number cannot erase `>`, `~`, or
`±`. Unicode typography and whitespace are normalized; chemical case, punctuation
and stoichiometry are preserved. The four standard performance names have explicit
aliases for PCE, Voc, Jsc and FF. There is **no** LLM judge, inferred chemical synonym
dictionary, or automatic interpretation of unfamiliar paraphrases.

Operation descriptions use a small, explicit equivalence map. The default maps
`thermal annealing` to `annealing`; it does not equate arbitrary heating, drying or
annealing descriptions. `--operation-aliases aliases.json` replaces that map with
an expert-approved JSON object, for example `{"thermal annealing": "annealing"}`.
An empty object disables aliases. Keys and values are whitespace/case normalized;
empty/conflicting entries and alias chains/cycles are rejected. The full map is
stored in every report and must match across an aggregate. Freeze it on development
papers before evaluating unseen papers.

Consequently, equivalent free-text operation descriptions, chemical synonyms,
alternative stack representations, or differently written ranges may score as
disagreements. Inspect such cases before freezing the benchmark protocol. Do not
tune aliases or tolerances against the held-out test results. Layer/step order comes
from explicit sequence fields, not JSON array position; unspecified ordering cannot
be reconstructed by the scorer. Record alignment remains an algorithmic estimate,
not proof of specimen identity.

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

The matcher uses a versioned, transparent lexical/content similarity and a Hungarian
assignment. Previously matched parent links and compatible protocol fields receive
an assignment preference, preventing equal numbers on different devices from being
paired purely by array order. The lexical threshold still applies; returned pair
similarities are the unboosted content scores. It does not greedily match records in file order. The threshold and
numeric tolerances are stored in every report. Do not tune them on the held-out test
split.

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

## Preparing a human-versus-pipeline A/B study

The scorer is source-blind: human and model outputs use the same `StudyExtraction`
schema, frozen truth, field selection and equality rules. Keep the two questions
separate: objective extraction accuracy against independent adjudicated truth, and
experts' blinded preference for the presented results.

Before collecting the comparison:

1. Freeze unseen paper assignments, main/SI access, figure policy, scoring version,
   tolerances and human time/tool allowance. Explicitly choose whether the question
   is equal-time performance or best achievable quality.
2. Have humans extract without seeing the model output or the reference labels.
   Do not use that same person's extraction as the only ground truth for its own score.
3. Independently adjudicate correctness and completeness. Treat figure-only facts
   under a declared shared scope; figure classifications alone are not field-level
   ground-truth labels.
4. Score both outputs unchanged. Retain failed papers: an empty valid extraction
   scores zero recall where truth contains facts; invalid or missing artifacts are
   explicit run failures, not papers silently dropped by the evaluator.
5. Present A/B results in the same layout, randomize sides, hide their origin, and
   ask separately about factual correctness, completeness, device/measurement
   attribution, chemical detail and correction effort. Include tie and cannot-judge
   responses.
6. Compare paired paper-level scores and review effort. The existing aggregator is
   not a paired-comparison analysis; add that analysis when the A/B assignments are
   fixed. Preserve both truth and prediction versions so later label corrections
   cannot masquerade as model improvements.
