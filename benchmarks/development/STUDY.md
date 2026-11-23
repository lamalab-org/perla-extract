# Frontier quality and extraction cost

Measure Astra against human references, then compare cheaper models and one
refinement workflow. **Paid execution starts after PR review.** Astra outputs
never become labels; saved pilots remain separate from primary results.

## Fixed inputs and methods

Use all 30 papers and 27 SIs in `papers.json`: 57 PDFs, 1,143 pages, with separate
article/SI hashes and a pinned human-finalized reference release. These are
model-assisted development references with existing coverage limitations;
no new human labeling is required.

| Deployment | Reasoning | Primary runs | Second runs |
| --- | --- | ---: | ---: |
| GPT-6.2 Astra | high | 30 | 10 |
| GPT-6.1 Terra | medium | 30 | 10 |
| GPT-6 Luna | medium | 30 | 10 |

Use Docling and production `parse_documents` to combine/deduplicate article and
SI. Reuse identical prepared evidence across models. Direct arms make one attempt,
with reader, refinement, repair and enrichment off; allow 180,000 estimated input
and 40,000 output tokens. Model and reasoning are deployment contrasts, not
model-only effects at equal compute. Record requested aliases and served IDs.

Preflight every paper before paid calls. Resolve capacity failures with a common
policy before freezing; never truncate or drop a difficult paper after scoring.
Freeze evidence, methods, prompts/schema, code/dependencies, references and order.
Use seed 20261122 to shuffle paper blocks and arms within each block. Each attempt
has an independent response cache. Finish 90 primary runs before the 30 repeats.

The fixed repeatability subset contains 3 short, 3 medium and 4 long papers by
combined PDF/SI pages. Within each length third, select by ascending
SHA256(`20261122:paper_id`); membership is committed in `papers.json`.
Second runs keep identical declared settings and measure API repeatability.

## Scoring and analysis

Freeze matcher **v26** / fact profile **v23**, tested on saved outputs before
inference. Preserve original evaluations. Use **1% relative / zero absolute**
numeric tolerance; unitless PCE/FF use percentage points (0.2 means 0.2%).

Report PCE, FF, Voc and Jsc individually, pooled PV fields, material/device
identity, processing and stability. Separate paper-wide recovery from strict
attribution. Retain malformed/literal claims in field denominators; diagnostic
claim types prevent labeling field agreement as numeric-only accuracy.
Report precision, recall, F1 and underlying counts; unsupported recall is unavailable.

Primary effects are equal-paper mean differences on the 30 first runs, with
95% paired-paper bootstrap intervals: 10,000 resamples, seed 20261122. Report the
10-paper repeatability analysis separately, averaging repeats within papers.
The independent unit remains the paper, never a fact or repeated run.

Use two percentage points of mean F1 loss as a development target, alongside
per-field recall and domain regressions. An interval crossing that target is
inconclusive; this sample cannot guarantee equivalence. Incomplete references
support annotation-agreement comparisons, not absolute accuracy or held-out rankings.
Show planned/completed/scored counts and failures. Any complete-pair estimate must
name its denominator; missing runs do not silently disappear or become zero quality.

## Refinement follow-up

Choose at most one cheaper draft model after the direct comparison. On the fixed
10-paper subset, compare direct cheaper/Astra results with one Astra review of the
cheaper draft. Optionally add Astra draft + Astra review if the budget permits.
Declare follow-up arms before requests and retain the same sources and scorer.

Use the existing refinement stage: full source plus draft, returning a complete
candidate. Reuse saved drafts where supported. Pipeline cost includes draft plus
review; campaign accounting charges each real provider attempt once. Measure
domain gains and collateral losses; refinement may cost more than direct Astra.
Enrichment creates separate export interpretations and is outside this extraction
comparison. No router or additional extraction stage is needed.

## Spending and artifacts

The approved **$150 campaign admission cap** retains **$11.38294587** in prior
recorded cost and **$17.45** in unknown-cost holds. Priority: primary runs, then
repeatability, then refinement. The earlier unexecuted four-paper Astra plan is
superseded; it incurred no calls or reservations.

Use one ledger and one active generative request. Persist a reservation before
dispatch, including known spending and unresolved holds in admission checks.
Settle only with complete accounting; otherwise retain the hold. A response can
exceed its estimated reservation. Stop new admissions at the cap and retain every
unfinished slot. Do not spend simply to complete a figure.

Capture full Langfuse traces and local requests, responses, configurations and
sealed manifests. Publish sanitized per-paper/field scores, failure codes and
costs with code/scorer versions. Cost projections require complete accounting and
exclude parsing/infrastructure costs. PDFs, prompts, credentials and private review
history stay outside Git. Use the existing extractor, scorer and comparator.
