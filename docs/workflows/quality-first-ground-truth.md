# Quality-first seeds, reviewed truth, and cost reduction

Optimize extraction quality and inference cost in that order. The first objective is a
high-recall, evidence-backed pre-annotation that makes human review efficient. The
second is adjudicated ground truth. Only then is there enough information to decide
which model calls can be made cheaper or removed.

```mermaid
flowchart LR
    A["Paper + complete SI"] --> B["Quality-first extraction"]
    B --> C["Immutable model seed"]
    C --> D["Record and figure census"]
    D --> E["Correction and record decisions"]
    E --> F["Completeness check"]
    F --> G["Adjudication"]
    G --> H["Frozen ground truth"]
    H --> I["Cost and model ablations"]
    I --> J["Cheapest configuration meeting quality targets"]
```

## 1. Generate the strongest practical seed

Use the quality-first defaults on the complete main paper and Supporting Information:

```bash
perla-extract \
  --pdf paper.pdf \
  --supplement paper_si.pdf \
  --output-dir results/paper
```

This profile combines five complementary safeguards:

1. Docling preserves document structure in parser-independent evidence blocks.
2. A neutral, source-grounded ledger separates experimental objects and atomic claims
   from final database records. Long papers use windows only to collect this ledger.
3. The frontier extraction model globally assembles one complete draft from the
   combined ledger and cited passages, followed by an evidence-complete reconciliation
   pass over the same sources. The alternate remains available for audit.
4. Deterministic validation checks exact evidence, atomic values, identifiers, and
   links.
5. One bounded repair call revisits only audit-visible gaps or exact unsupported
   records using implicated text/table blocks; separate enrichment calls propose
   composition and processing interpretations.

Refinement and repair are checked against the immutable draft before the seed is
written. A candidate may not increase validation or semantic claim-coverage issues.
The gate deliberately does not demand at least as many records or values: an
unsupported extra family is an error, and removing it must be allowed. No weighted
score trades evidence correctness for a larger output.

No stage sends rendered pages to a vision model. Parser failures in chemical notation
remain visible for review instead of being silently reconstructed from an image.

All extraction and refinement requests cite deterministic source-span IDs. Python
restores exact quotations afterward, so the public `StudyExtraction` still contains
ordinary nested citations without asking a model to reproduce source text.

Do not select a seed merely because it has more records or because validation passes.
Source verification proves that text exists, not that it has the correct semantic role.
If multiple candidates are available, preserve their reports and use their disagreements
as reviewer attention cues. Never merge records by identifier alone because independent
runs may name the same scientific entity differently.

## 2. Admit a seed to review

For a versioned cohort, run every paper through one frozen configuration rather than
assembling an undocumented shell loop:

```bash
perla-extract-cohort \
  --manifest data/study_extraction/cohorts/review-v1.json \
  --pdf-dir /path/to/main-papers \
  --supplement-dir /path/to/supporting-information \
  --output-dir results/review-v1 \
  --env-file /path/to/provider.env
```

The command resumes only seeds whose model, parser, schema hash, prompt hash, and
claim-recall setting still match. It writes `cohort_run.json` after every paper so an
interrupted batch remains auditable. The tracked `review-v1` cohort is development
data because its papers have already informed extractor design. A final test cohort
must be sampled later from genuinely unseen Zotero submissions and frozen before its
outputs are inspected.

Independent workers may share the batch without duplicating papers by supplying the
same `--shard-count` and a different zero-based `--shard-index`. Each worker writes a
separate audit file; document and model caches remain content-addressed.

Before import, require:

- a schema-valid `extraction.json`;
- `validation.json` with no unresolved evidence issue;
- `document.json`, `run_configuration.json`, and `report.json`;
- `claim_ledger.json`, `claim_coverage_audit.json`, and `refinement_audit.json`;
- `targeted_repair.json`, including its worklist and acceptance decision; and
- the exact main-paper and supplement hashes.

Keep enrichment decisions in `enrichment.json`. The machine-readable status `accepted`
means only that a deterministic proposal passed automated consistency checks. It may
support NOMAD export, but it is not a human verification and does not rewrite the
source-reported ground truth. The workbench therefore displays this state as **Passed
automated checks**.

Imported seeds are immutable. If a better extraction is produced before review begins,
create a new review item or explicitly archive the unused seed; do not silently replace
its provenance. Once any human decision exists, a new model output is a proposal, never
a replacement for the reviewed revision.

When replacing a historical dataset generation, preserve its mutable state under a
versioned, read-only legacy path, retain its source documents, and import current seeds
into a separate split or dataset namespace. Schema readability is not evidence that
two generations are comparable, and a legacy flat-schema draft must never be rewritten
in place as if it had been produced by the current rich extractor.

The workbench compares each seed's recorded schema version and schema hash with the
running extractor. A readable older seed remains available for review, but a visible
warning marks it as non-identical: default-valued migration cannot recover fields the
older extraction never attempted to produce. Regenerate an untouched seed, or review
the newly added fields explicitly; never treat schema readability as evaluation
comparability.

## 3. Review records and measure completeness

The reviewer may inspect the extracted records immediately and correct them beside the
paper. The Census tab records the corrected paper-wide totals and separately counts
main-text figures, schema-relevant figures, and schema records or atomic values that
occur only in those figures. The record totals are therefore model-assisted and must
not be reported as a blind recall estimate. The figure counts measure the narrower loss
from text-only extraction without conflating it with whether the reviewer searched the
main paper or SI. Review then proceeds through:

1. entity identity and reporting level;
2. composition, layers, and processing;
3. performance, population statistics, and stability;
4. exact evidence and atomic-value checks; and
5. a final search for omissions.

Every current record receives a decision. Corrections require source evidence; an
uncertain relationship remains unresolved instead of being guessed. An administrator
adjudicates disagreements and freezes the final revision using the procedure in
[Build ground truth](ground-truth-review.md).

## 4. Protect evaluation splits

Prompt development, parser changes, schema design, model selection, and manual error
inspection all expose a paper. Such papers belong to calibration or development, even
if an older folder called them `test`. Reserve a newly sampled, independently reviewed
set for final evaluation after the workflow is frozen.

Sample across publisher, architecture, chemistry, table density, SI length, stability
reporting, and parser difficulty. Record exclusions such as reviews, news, views, and
perspectives before extraction.

## 5. Reduce cost against frozen truth

Run controlled ablations with identical sources, parser output, schema, scoring code,
and cache policy. Change one component at a time:

1. refinement model or `--no-refinement`;
2. claim-collection model or `--no-claims`;
3. `--claim-recall-passes 1` versus the quality-first default of two;
4. enrichment model or `--no-enrichment`;
5. primary extraction model; and
6. parser backend or claim-reading window budget.

Measure at least:

- precision and recall for families, devices, observations, populations, and stability
  tests;
- field agreement for correctly matched records;
- composition and processing agreement;
- evidence-link validity and atomic-value violations;
- NOMAD projection coverage and conversion issues; and
- prompt tokens, completion tokens, latency, and cost per paper.

Do not optimize a single aggregate score. In particular, lower cost is not acceptable
when it systematically removes devices, chemical detail, or uncommon stability
conditions. Select the cheapest configuration whose per-category quality bounds remain
acceptable on development data, then evaluate it once on the held-out test set.
Use `perla-evaluate` for immutable paper reports and `perla-evaluate-dataset` for
compatible micro/macro aggregation; do not implement separate matching logic in an
experiment notebook.

## Measured calibration example

On one 6-page Science paper with a 35-page supplement, citation-catalog compaction
reduced the refinement input from 174,116 to 84,778 tokens and the refinement charge
from about $2.20 to $0.79. A fresh claim ledger, primary draft, and compact refinement
would cost approximately $1.57 before optional enrichment for the higher-recall run.

This is a calibration observation, not a universal price estimate or quality result.
Model outputs varied from 252 to 388 source-verified atomic values despite complete
source grounding, demonstrating why adjudicated field-level recall—not validation
status or record count—must govern later cost decisions.
