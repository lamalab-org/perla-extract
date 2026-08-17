# Quality-first seeds, reviewed truth, and cost reduction

Optimize extraction quality and inference cost in that order. The first objective is a
high-recall, evidence-backed pre-annotation that makes human review efficient. The
second is adjudicated ground truth. Only then is there enough information to decide
which model calls can be made cheaper or removed.

```mermaid
flowchart LR
    A["Paper + complete SI"] --> B["Quality-first extraction"]
    B --> C["Immutable model seed"]
    C --> D["Blind inventory"]
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

This profile combines five independent safeguards:

1. Docling preserves document structure in parser-independent evidence blocks.
2. A value-free inventory searches for reporting-level candidates and guides recall.
3. The frontier extraction model creates a complete draft, followed by an
   evidence-complete refinement pass over the same sources.
4. Deterministic validation checks exact evidence, atomic values, identifiers, and
   links.
5. One bounded repair call revisits only audit-visible gaps using implicated parser
   text/table blocks, then monotonic gates prevent it from trading away grounded data;
   separate enrichment calls propose composition and processing interpretations.

No stage sends rendered pages to a vision model. Parser failures in chemical notation
remain visible for review instead of being silently reconstructed from an image.

The refinement request normalizes repeated evidence into one citation catalog. This is
a lossless transport optimization: the public `StudyExtraction` still contains ordinary
nested citations after expansion.

Do not select a seed merely because it has more records or because validation passes.
Source verification proves that text exists, not that it has the correct semantic role.
If multiple candidates are available, preserve their reports and use their disagreements
as reviewer attention cues. Never merge records by identifier alone because independent
runs may name the same scientific entity differently.

## 2. Admit a seed to review

Before import, require:

- a schema-valid `extraction.json`;
- `validation.json` with no unresolved evidence issue;
- `document.json`, `run_configuration.json`, and `report.json`;
- the independent `coverage_audit.json` and `refinement_audit.json`; and
- `targeted_repair.json`, including its worklist and acceptance decision; and
- the exact main-paper and supplement hashes.

Keep enrichment decisions in `enrichment.json`. An accepted deterministic proposal may
support NOMAD export, but it does not rewrite the source-reported ground truth.

Imported seeds are immutable. If a better extraction is produced before review begins,
create a new review item or explicitly archive the unused seed; do not silently replace
its provenance. Once any human decision exists, a new model output is a proposal, never
a replacement for the reviewed revision.

## 3. Review without anchoring recall to the model

The reviewer first records a blind paper-wide inventory. Model candidates and coverage
audits remain hidden until that census is submitted. Review then proceeds through:

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
2. inventory model or `--no-inventory`;
3. enrichment model or `--no-enrichment`;
4. primary extraction model; and
5. parser backend or long-document window budget.

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

## Measured calibration example

On one 6-page Science paper with a 35-page supplement, citation-catalog compaction
reduced the refinement input from 174,116 to 84,778 tokens and the refinement charge
from about $2.20 to $0.79. A fresh inventory, primary draft, and compact refinement
would cost approximately $1.57 before optional enrichment for the higher-recall run.

This is a calibration observation, not a universal price estimate or quality result.
Model outputs varied from 252 to 388 source-verified atomic values despite complete
source grounding, demonstrating why adjudicated field-level recall—not validation
status or record count—must govern later cost decisions.
