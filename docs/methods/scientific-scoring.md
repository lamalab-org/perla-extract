# Deterministic evaluation of scientific extractions

This method measures agreement with a fixed reference. It does not use an LLM
judge. Source completeness and citation support require separate review. The
current contract is evaluation format 11, matcher `rich-study-hungarian-v15`, fact
profile `core-scientific-facts-v12`, and explanation format 5. The extraction schema
and ground-truth format are unchanged.

## Evaluation unit and two score views

Each input is independently projected into scientific facts. A fact records a
concept, complete reported value, owner, claim-defining context, reporting domain,
and original JSON paths. Generated IDs, display labels and empty placeholders do
not themselves earn points. Multiple copies within a list remain separate
assertions. Equal redundant assertions merge only across an explicit link: a
layer and its sole linked absorber, or a device's structured treatment conditions
and reported properties. Merging preserves every source path and permits only
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
ambiguity; their affected facts receive no attributed credit. An inventory pair
is a candidate correspondence, not evidence that all of its scientific fields
agree.

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
credit. Unknown protocol names remain literal facts; the checked protocol registry
determines which metric fields constrain other outcomes. If both
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
The fixtures are assistant-inspected development controls; independent
adjudication is explicitly pending. A higher aggregate F1 is not a validation gate.

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
