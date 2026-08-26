<!-- generated-by: gsd-doc-writer -->
# PERLA Extract

PERLA Extract creates an evidence-backed representation of every photovoltaic device
reported in one paper and its Supporting Information. The core design decision is to
model the study first and export flat rows only afterward. This prevents a champion
cell, an average over many cells, and a stability specimen from being silently treated
as the same measurement.

## System at a glance

```mermaid
flowchart LR
    A["Paper + Supporting Information"] --> B["Parser-independent evidence blocks"]
    B --> C{"Claim evidence fits one model context?"}
    C -->|Yes| D["Collect source claims once"]
    C -->|No| E["Collect claims in structural windows"]
    D --> F["Ground and combine claim ledger"]
    E --> F
    F --> G["One global study assembly"]
    G --> R["Evidence-complete reconciliation"]
    R --> H["Rich StudyExtraction"]
    H --> I["Citation checks and claim-coverage audit"]
    I --> T["Targeted text/table repair with semantic gates"]
    T --> L["Audited composition and processing enrichment"]
    L --> J["Atomic NOMAD archives"]
    H --> K["Human ground-truth review"]
```

The parser retains source, page, section, block text, and—when available—page
coordinates. The model returns strict Pydantic records with exact evidence quotes.
Local checks then report unsupported quotes, raw values that are absent from their
cited evidence, duplicate identifiers, and dangling links. These checks annotate the
result; they do not erase model output.

## Design principles

- **Keep scientific reporting levels separate.** Individual measurements,
  population statistics, and stability experiments are different record types.
- **Separate reading from record construction.** Long inputs may use structural
  windows to collect neutral source claims, but one global call constructs the final
  records from their combined ledger and cited passages.
- **Re-read before review.** A second pass audits the complete draft against the same
  evidence. The first draft and a record-level change index remain inspectable.
- **Treat scope as data.** Study targets, processing arms, characterization specimens,
  populations, and measurements remain distinct before any are mapped to records.
- **Use generic reported values.** Layers and processing steps contain `ReportedValue`
  records. Every value denotes one semantic quantity, while shared citation IDs avoid
  repeating the same evidence. Property-specific regular expressions do not decide
  what can be extracted.
- **Make uncertainty inspectable.** The full output, conservative grounded subset,
  failed responses, configuration, and conversion losses are separate artifacts.

## Choose a path

- [Run your first extraction](getting-started.md)
- [Discover new papers with PapersBot](workflows/papersbot.md)
- [Understand the study model](concepts/study-model.md)
- [Understand evidence validation](concepts/evidence.md)
- [Review and curate ground truth](workflows/ground-truth-review.md)
- [Create quality-first review seeds, then reduce cost](workflows/quality-first-ground-truth.md)
- [Interpret composition and processing](workflows/enrichment.md)
- [Export directly to NOMAD](workflows/nomad-export.md)
- [Export to the historical reduced PERLA schema](compatibility/reduced-schema.md)
