# Deterministic evaluation of scientific extractions

This method measures agreement with a fixed reference. It does not use an LLM
judge. Citation checks are separate diagnostics; this method does not establish source completeness. The
current contract is evaluation format 12, matcher `rich-study-hungarian-v17`, fact
profile `core-scientific-facts-v14`, and explanation format 6. Extraction schema 8 adds optional measurement conditions. Ground-truth format 4 is unchanged.

## Evaluation unit and two score views

Each input is independently projected into scientific facts. A fact records a
concept, complete reported value, owner, claim-defining context, reporting domain,
and original JSON paths. Generated IDs, display labels and empty placeholders do
not themselves earn points. Multiple copies within a list remain separate
assertions. Equal redundant assertions merge only across an explicit link: a
layer and its sole linked absorber, or a device's structured treatment conditions
and reported properties, or registered measurement conditions repeated across the
legacy metrics and measurement_conditions arrays. Merging preserves every source path and permits only
unit-conversion roundoff (16 machine epsilons relative, zero absolute tolerance).
Conflicting linked assertions are flagged rather than treated as alternative
answers.

The same inventory supplies two separately computed, one-to-one assignments:

- **Attributed agreement** requires a compatible concept, equivalent value,
  compatible context and an unambiguous owner.
- **Paper-wide recovery** requires a compatible concept and equivalent value.
  It can match different owners or record containers. It measures information
  recovered somewhere in the paper, not correctly attributed extraction.

For either view, let `T` and `P` be reference and predicted fact counts and `M` be
the assigned matches. Precision is `M/P`, recall is `M/T`, and F1 is `2M/(P+T)`.
A zero denominator is reported as undefined. An incorrect assertion can therefore
contribute both a missed reference fact and an extra predicted fact. Counts and
scores are reported overall and by domain/subfield; domains do not select matching
candidates. Repeated assertions cannot share one matching fact within a view.

## Establishing identity before evaluating outcomes

Top-level records are paired by maximum-weight one-to-one assignment. Identity
features exclude measured outcomes. Competing optimal assignments are retained as
ambiguity; facts depending on an unresolved scientific subject receive no attributed
credit. An inventory pair
is a candidate correspondence, not evidence that all of its scientific fields
agree.

An individual performance observation with an explicit device link is a container
of facts about that device. Its facts therefore use the device as owner and retain
the full treatment, measurement-type, scan and asserted-protocol requirements.
Arbitrary pairing of observation IDs is unnecessary. The existing fact assignment
compares multisets within this scientific context: splitting, merging or reordering
observation containers does not change credit when their facts and context are
unchanged. Duplicates retain multiplicity and cannot share one reference fact.
Missing or ambiguous device links still withhold credit. This evaluates marginal
reported facts, not joint metric vectors or correspondence between experimental
replicates. Population and stability-test records retain their own identity;
identical treatment descriptions alone do not establish the same cohort or test.

For families with distinct, explicit layer positions on both sides, materials
are compared in order. Let `L` be the length of their longest common material
subsequence, and `n,m` their respective layer counts. The family similarity is
`(2L + V)/(n + m + 1)`, where `V=1` only for equal, nonempty variant labels after
spacing normalization. A missing or different label contributes zero.
Thus exact arm labels can distinguish otherwise identical stacks, while lengthy
prose contributes no additional weight. Candidate similarity must reach **0.35**.
The threshold is a fixed matching policy, not a calibrated probability.

If an order is absent or incomplete, the existing conservative family-feature
comparison is used. No missing order is inferred. Within a paired, fully ordered
family, only layer links present in **every** longest ordered alignment establish
correspondence. Absolute numbering is not compared. An omitted layer remains
unmatched; it does not shift ownership of all later layers. The same links scope
material/composition facts, absorber references and fabrication targets. Repeated
indistinguishable layers remain unresolved. Recipe steps use the analogous
order-preserving comparison of operation and target, not temperature or yield.

Material normalization covers a checked vocabulary and bounded notation rules.
It preserves chemical case, stoichiometry, treatment prefixes, explicit form and
unknown qualifiers. It does not expand an arbitrary mixture or infer a missing
dopant. Roles, material forms and compositions still require their own fact credit.
Family correspondence based on a partial stack does not certify the full design.

## Treatment identity and context

For an individual device, recognized treatment inputs are read from both
`subject_context.conditions` and `reported_properties`. Each study supplies its
own interpretation; the reference never fills gaps in a prediction. Registered
input names and complete quantities establish a treatment description. The stage,
treated material, champion status and selection basis remain explicit requirements.
A variant label is redundant when explicit registered inputs supply identity;
otherwise its literal wording remains required. Population treatment sets use the
same conservative quantity comparison and retain multiplicity.

Unknown structured conditions remain required context but cannot identify a
subject or break an assignment tie. In particular, putting PCE in a condition
field cannot make the outcome identify its own subject. A missing distinguishing
condition is unresolved, not a wildcard. Different treatment, specimen, scan
direction, statistic or registered measurement-protocol condition blocks attributed
credit. Explicit measurement conditions constrain outcomes, including unfamiliar names
retained literally. For archived records, only registered protocol names in metrics
are interpreted as measurement conditions. Conditions in preparation or aging
containers never supply missing measurement context. If both
inputs omit a qualifier, equality of that omission does not establish the actual
experimental condition. Sample count is separately scored exactly: an omitted
count loses its own credit; explicitly conflicting counts block cohort outcomes.

Explanations distinguish **equivalent**, **contradictory** and **unresolved**
context. They consume the same comparison decisions as the score. Candidate
display limits cannot affect assignment or credit. Missing owners, ambiguity,
wrong values and one-to-one competition are reported separately.

A source-backed regression example is the 5.9% champion PCE in
`10.1021/acs.chemmater.8b01521`. Its reference stores four preparation inputs as
device properties; a saved extraction stores those inputs as structured treatment
conditions and uses a longer variant description. Those representations now agree.
Changing the reduction duration, dropping a distinguishing input or changing the
scan still prevents attributed credit. This control establishes one repaired
comparison, not correctness of the entire extraction or its citations.

## Fact-local requirements

`scoring_requirements.py` is the dependency table used by projection, comparison
and explanations. A rule removes only its named independent attributes. Unknown
context remains a requirement; other domains retain their existing strict rules.

| Field | Subject and defining context | Independently scored detail |
| --- | --- | --- |
| Individual PCE, Voc, Jsc, FF | Specimen, family, treatment/arm, selection, individual result kind, measurement type, scan, asserted protocol | Unrelated recipe annotations |
| Annealing temperature/duration | Family, aligned target and ordered annealing step | Optional ingredients of the established step |
| Spin-coating speed/duration | Family, aligned target and ordered coating step | Optional ingredients of the established step |

The processing rule operates on canonical names, so `speed` and `spin-coating
speed` have the same dependencies. The target value never establishes step
identity. Without an established ordered step, the conservative context remains.
Population and stability outcomes do not inherit individual-measurement rules.

The scorer does not compare citation block IDs or quoted numbers to establish
identity. Its inputs lack the independently validated, shared source locations
needed for precise observation anchors. Diagnostics therefore report source
anchors as unavailable. A common paragraph, matching outcome, or device link does
not justify ignoring missing measurement conditions. Both sides omitting a
condition measures agreement in the recorded context, not proof of experimental
completeness.

```text
project each study independently into facts with original paths
align subjects and ordered steps without measured outcomes
for each pair of facts with the same scientific concept:
    apply that fact's declared context requirements
    compare the complete value (including units and qualifiers)
    retain value eligibility and attributed eligibility separately
compute a maximum one-to-one assignment for each view
report both scores over the same complete fact inventories
```

Known concepts fix their reporting denominator. A malformed or qualitative PCE
remains in `photovoltaic_metrics`; `value_interpretation` separately distinguishes
quantities from literal claims. This bin includes both individual and population
claims; `statistical_level` in diagnostics distinguishes them. It must not be
described as a validated numeric-only or individual-only subset.

## Automatic diagnostics and software controls

Every fact has a `rule_id`, parsing status, candidate count and attribution-context
availability. Availability means at least one unambiguous candidate satisfies
context, independently of value agreement. It is not a confidence probability or
source-completeness measurement. Unmatched facts record overlapping candidate
blockers: wrong value, missing owner, ambiguous owner, missing defining context,
conflicting context, no candidate and one-to-one competition. Blockers inspect
same-owner candidates when available; otherwise they inspect all same-concept
candidates. They do not claim that one selected rejection caused a score change.

`examples/scoring/validate.py` tests the frozen references and bounded synthetic
derivatives. `calibration.py` isolates a measurement with its linked specimen and
family, records source-path lineage, and explicitly supplies synthetic type, scan
and protocol context. Each transformation declares allowed and forbidden matches
before evaluation. Unit arithmetic, missing/conflicting context, misplaced
protocols, renamed IDs, duplicate predictions, corrupted quantities and composed
changes have known expected effects. Inapplicable transformations are counted,
and failures retain their derived inputs. These checks require no new human labels.
Existing tests additionally cover cohort statistics, aging versus preparation,
wrong owners, uncertainty, ordered alignment and exhaustive tiny assignment oracles.
All reference papers are development data; passing controls estimates no natural
prediction error rate.

Normalization caches only immutable text and quantity tuples, with bounded caches.
Owner mappings and assignments are recomputed for each prediction. Dense candidate
work is partitioned by scientific concept, without approximate numeric buckets.
A bucket exceeding one million pairs raises a scoring error before assignment;
no truncated score is emitted. Explanations are bounded independently of scoring.
The saved-study replay uses bounded paper workers and one verified reference per
paper. Serial/parallel and cached/uncached ledgers must agree; this does not imply
linear scaling of dense assignment.

## Numerical and conceptual equivalence

Scalar values are converted to compatible canonical units and compared using
`abs(a-b) <= 0.01 * max(abs(a), abs(b))`: **1% relative tolerance, zero absolute
tolerance**. Counts require exact equality. Ranges and uncertainties must match
as complete claims; a matching central number alone is insufficient. Offset units
are converted before comparison. Bounds, unknown units and contradictions between
raw text, parsed number and unit are not silently discarded. Unsupported claims
remain literal assertions.

The grammar accepts printed powers of ten and a single variable assignment such
as `E = 1.0 V` or `Q_pol = 10 mC/cm2`; it still validates the complete right-hand
side. Scientific concept aliases include a consistent full name/abbreviation pair
such as `power conversion efficiency (PCE)`. Conflicting pairs, relative versus
absolute efficiency, initial versus final conditions, and recipe versus finished
material remain distinct. Exact missing-data placeholders without a number or
unit are omitted from both inventories; a statement such as “not detected” is
retained.

## Validation, reference revisions and reporting

Semantic controls test unit/notation invariance, ID changes, record order,
duplicate predictions, omitted layers, wrong values, wrong owners and ambiguous
assignments. Real saved prediction/reference cases supplement synthetic controls.
The fixtures are development controls, not independently labeled accuracy data. A higher aggregate F1 is not a validation gate.

Scorer changes are tested by replaying identical saved extractions. Reference
corrections are isolated in four cells: original/revised scorer crossed with
original/proposed reference. The frozen checkout is an audit tool, not a second
production scorer. Reports bind predictions, references, configuration and run
seals to hashes. Changed-decision and inventory ledgers separate new credit,
removed placeholders, merged representations and changed assignments. A first
reported rejection is not automatically the cause of a score change.

The review sampler draws without replacement within view, side, domain,
awarded/denied credit and changed/unchanged-decision strata. It records inclusion
probabilities in a separate analyst key and leaves human judgments blank. The
same fact can appear in both views; those decisions are not independent facts.
Targeted debugging controls must not be pooled with probability samples to
estimate scorer error. Reviewing source omissions is also necessary to establish
recall; inspecting predictions alone cannot do that.

Report extraction completion, attributed agreement, recovery and citation/source
validation separately. Preserve failed attempts as missing scores. Average repeats
within a paper before comparing methods, and resample papers rather than facts
for uncertainty intervals. State the number of complete paired papers and the
reference coverage. Without independent completeness and context review, these
scores are **development reference agreement**, not validated extraction accuracy.

`perla-compare` produces a diagnostic `reference_agreement` contrast automatically
from complete, verified pairs, even when coverage is unreviewed or some facts have
unresolved attribution. Such facts retain zero attributed credit and remain in
the denominator. The stricter `quality` conclusion still requires reviewed scope
and resolved alignment. Both contrasts enforce the same source, scorer, reference,
configuration and design compatibility checks. A subset contrast lists omitted
pairs and is explicitly conditional on complete paired repetitions; it does not
estimate performance on the failed attempts. No per-prediction review is required
to run or compare ablations.
