# Frontier quality and extraction cost

Establish a frontier baseline against the existing human references, then measure
how close cheaper deployments and one simple mixed-model workflow can come.
Astra outputs never become labels. The development roster is fixed before this
study; previous pilot outputs remain separate from its primary results.

## Dataset

Use all **30 papers** in `papers.json`, including all **27 supplied SIs**. Their
57 PDFs total 1,143 pages. Each source role has its own hash; the pinned public
reference release supplies the ground-truth and manifest hashes. Do not substitute
another PDF download, omit a difficult SI, or redefine a subset as held out.
All references are human-adjudicated, model-assisted development records. Existing
incomplete coverage/context-review flags remain visible. No new human labeling or
model-generated adjudication is required for this development study.

The fixed repeatability subset contains 10 papers: 3 from the shortest third,
3 from the middle third and 4 from the longest third by combined PDF/SI pages.
Within each third select by ascending SHA256(`20261122:paper_id`). Selection uses
source length, not extraction outcomes. Membership is stored in `papers.json`.

## Direct-extraction comparison

| Deployment | Reasoning | Primary papers | Additional repeats |
| --- | --- | ---: | ---: |
| GPT-6.2 Astra | high | 30 | 10 |
| GPT-6.1 Terra | medium | 30 | 10 |
| GPT-6 Luna | medium | 30 | 10 |

That is **90 primary runs**, then **30 additional repeatability runs**. The unit
for statistical inference is the paper: **30**, not 120 runs or thousands of
individual fields. Model and reasoning settings are deployment contrasts, not
equal-compute model-only effects. API aliases and actual served model IDs must be
recorded; neither establishes a frozen weight revision.

Parse every article/SI with the same Docling implementation, then use production
`parse_documents` to combine, deduplicate and attach context. Prepare once per
paper and reuse identical evidence across deployments and repeats. Never build
model input from the reference annotations. Use the current schema/prompt,
40,000 output tokens and a 180,000 estimated-input limit. Disable reader,
refinement, repair and enrichment for all direct arms. `astra.toml` is the
ordinary configuration; cheaper deployments change only model and reasoning.

Preflight the **whole roster** before inference. If a full paper/SI does not fit,
resolve and record a common capacity policy before freezing the study; do not
truncate evidence or remove that paper after seeing its score. Freeze prepared
inputs, code/dependency versions, methods, references and execution order. Use
seed 20261122 to shuffle paper blocks and the three deployments within each
paper. Finish the primary roster before optional repeats or hybrid tests.
Each run owns an independent response cache and provider attempt. Repeats retain
the same declared generation settings; they assess API repeatability, not extra
independent papers or a sampling distribution over deliberately changed seeds.

## Scoring prerequisite and analysis

Before freezing the new study, fix the known qualified-metric normalization gap
through the shared scoring boundary: `certified power conversion efficiency`
must remain PCE with its certification qualification preserved. Test equivalent
forms and genuine contradictory qualifiers symmetrically; add no paper-specific
rules. Audit numeric versus qualitative PV assertions so plots are labeled
correctly. Version the scorer once and rescore the saved pilots without inference
to verify the behavior. Preserve all original evaluations. No paid study calls
precede this check. The present repository baseline is matcher v25 / fact profile
v22; the study must record the final tested scorer identifiers before starting.

Keep 1% relative / zero absolute numeric tolerance and the existing unitless
PCE/FF percentage-point convention. Score every completed run against the same
human reference. Report PCE, FF, Voc and Jsc individually, pooled PV fields,
material/device identity, processing and stability. Separate paper-wide retrieval
from strict device/context attribution. Preserve numeric, qualitative and
unparsed-claim counts; do not silently discard malformed predictions. Unmatched
claims can expose scoring representation gaps as well as extraction omissions.

Primary analysis uses the first run on all 30 papers. For each model contrast,
compute per-paper differences, weight papers equally and report 95% paired-paper
bootstrap intervals (10,000 resamples; seed 20261122). Report pooled precision,
recall, F1 and all denominators alongside those intervals. Fields with zero
reference support have an explicit unavailable recall; they are not successful
recoveries. Statistical panels must distinguish numerical PV claims from broader
field-name agreement. Inspect the fixed 10-paper second runs separately for
within-paper repeatability and failure changes. Do not pool them as 10 new papers.

Use two percentage points of paired mean F1 loss as a development target, with
per-field recall and broader-domain regressions visible. A confidence interval
crossing the target is inconclusive; 30 papers do not guarantee enough power to
establish equivalence. These are development comparisons with existing coverage
limitations, not validated absolute precision or held-out generalization claims.

Retain every planned slot, failed attempt, validation warning and unknown cost.
Give planned/completed/scored counts and completion rates for each deployment.
Any score conditional on complete pairs must name that denominator; do not make
a failure disappear by replacing its slot or silently treating it as zero quality.

## One mixed-model comparison, after measuring the gap

Choose at most one cheaper draft model from the direct comparison. On the fixed
10-paper subset compare its direct output, Astra direct, and one refinement by
Astra using the existing refinement stage. An Astra-draft/Astra-refinement arm
may be added as a quality-oriented review control if the budget admits it.
Declare these arms before their requests. This stage uses full source plus draft
and returns a full study; it may cost more than direct Astra. Do not assume a
hybrid is cheaper, add a router, or introduce another extraction stage.

Reuse immutable saved drafts where the existing stage contract permits. Attribute
draft plus review cost to the hypothetical pipeline even when the draft is reused;
charge its real provider attempt only once to the campaign ledger. Include full
tokens, cached input, reasoning, latency, failed attempts and completion. Report
which domain improves, not only an overall score. The hybrid analysis is an
explicit follow-up on development data, not an independent validation of selection.

## Budget and release

The user approved a **$150 total campaign admission cap on 22 November 2026**.
Carry **$11.38294587** known cost and retain **all $17.45** previous unknown-cost
reservations: initial commitment **$28.83294587**, leaving **$121.16705413** for
new admissions. The earlier unexecuted four-paper Astra plan is superseded by
this larger study; no calls or reservations were made for that plan.

Use one shared ledger and one active generative request. Before dispatch include
all known cost, open reservations and retained unknown-cost reservations in the
commitment check. Reserve per slot using its preflight input/output allowances
and current provider rates, with a recorded margin. Settle only with complete
usage/cost; otherwise retain the reservation. Do not treat unknown cost as zero.
One observed-cost threshold or reservation cannot guarantee the final bill.
Stop admissions before the approved cap; never spend to complete a figure.

Budget priority is the 90-run primary comparison, then 30 repeatability checks,
then the mixed-model follow-up. The cap is a limit, not a spending target or a
promise that every optional arm will fit. If it stops the study, retain the full
roster and report unfinished work. Do not silently shrink the cohort. Project
costs for 10,000/50,000 completed outputs only with complete cost accounting and
state the excluded parsing/infrastructure costs.

Commit the roster, protocol and reusable checker before inference. Validate source
bindings locally and reference integrity in CI. Capture full Langfuse prompts and
responses, and keep local requests, responses, configurations and sealed manifests.
Publish sanitized per-paper/field scores, failure codes and cost tables with the
implementation commit and scorer version. Keep paper PDFs, raw prompts, credentials
and private review history outside Git. Use existing extraction, scoring and
comparison modules; this plan does not introduce a new experiment configuration
format or a second scoring implementation.
