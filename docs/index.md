<!-- generated-by: gsd-doc-writer -->
# PERLA Extract

PERLA Extract turns photovoltaic papers and supporting information into source-linked
records for device structure, composition, processing, performance and stability.
It represents individual measurements separately from population statistics and
stability tests. Experts still need to check attribution, interpretation and completeness.

## Workflow

```mermaid
flowchart TD
    A[Paper and supporting information] --> B[Parse text and tables]
    B --> C[Read source into notes - optional]
    C --> D[Build study records]
    D --> E[Revise and repair - optional]
    E --> F[Check source links and save outputs]
    F --> G[Automatic scoring against a frozen reference]
    G --> H[Compare experiment settings]
    F --> I[Optional interpretation and export]
    F --> J[Optional expert review and reference curation]
```

The parser retains source, page, text and available coordinates. Models select
evidence-span IDs; the application restores exact quotations. Source checks detect
invalid citations, absent raw values, duplicate identifiers and broken links. They
do not establish that a passage scientifically supports a claim.

Finalization can remove unsupported optional claims. It preserves the complete
candidate and a list of removals for expert review.
See [Evidence and validation](concepts/evidence.md) for the checks and their limits.

Start with an [extraction](getting-started.md) or
[run an experiment](workflows/extraction-ablations.md) from one configuration file.
The optional [offline example](workflows/scoring-example.md) exercises scoring without
a model. The [maintainer guide](development/maintaining.md) maps operations to code.
The [evaluation method](methods/benchmark.md) explains the scientific contract.

## How extraction works

The quick start builds records directly from the complete prepared paper and SI.
An optional source reader first collects cited notes; it can split long documents
at structural boundaries and read them more than once. One global call then
assembles the study. An optional later pass checks the draft against the evidence.
Saved drafts and change reports show what each pass changed. The Python API's
defaults enable these extra stages; the quick start disables them explicitly so
their cost and benefit can be tested separately.

Claims distinguish device designs, processing variants, specimens and measurements.
Layers and processing steps use `ReportedValue` for individual quantities, with
shared citation IDs to avoid repeating quotations. The output retains failed
responses, configuration and export losses alongside the final study.

## Choose a path

- [Run your first extraction](getting-started.md)
- [Discover new papers with PapersBot](workflows/papersbot.md)
- [Understand the study model](concepts/study-model.md)
- [Understand evidence validation](concepts/evidence.md)
- [Review and curate ground truth](workflows/ground-truth-review.md)
- [Score rich extractions deterministically](workflows/evaluation.md)
- [Extract a batch for review](workflows/quality-first-ground-truth.md)
- [Interpret composition and processing](workflows/enrichment.md)
- [Export directly to NOMAD](workflows/nomad-export.md)
- [Export to the historical reduced PERLA schema](compatibility/reduced-schema.md)
