<!-- generated-by: gsd-doc-writer -->
# Python API

## Run an extraction

`extract_study` exposes the same settings as the command line and returns the final
report dictionary. LiteLLM resolves the provider and credentials from the model prefix
and environment. A named env file can supply provider variables; existing process
variables take precedence.

```python
from perla_extract.study_extraction.cli import extract_study

report = extract_study(
    pdf="paper.pdf",
    supplement="paper_si.pdf",
    output_dir="results/paper",
    parser="docling",
)
```

The quality-first default performs a second evidence-complete refinement. Set
`use_refinement=False` for a cost ablation, or set `refinement_model` to test a
different model while keeping the primary draft fixed through the response cache.
`use_targeted_repair=False` disables the bounded audit-driven recovery call;
`repair_model` changes only that call.

::: perla_extract.study_extraction.cli.extract_study
    options:
      show_root_heading: true
## Validate the rich schema

```python
from pathlib import Path

from perla_extract.study_extraction import StudyExtraction

study = StudyExtraction.model_validate_json(
    Path("results/paper/extraction.json").read_text()
)
```

::: perla_extract.study_extraction.models.StudyExtraction
    options:
      show_root_heading: true
      members: false

## Inspect enrichment

`EnrichmentAudit` keeps semantic interpretation separate from reported facts. The
default workflow writes it as `enrichment.json` before running downstream adapters.

::: perla_extract.study_extraction.enrichment.EnrichmentAudit
    options:
      show_root_heading: true

## Export to NOMAD

`to_nomad_with_report` returns separately uploadable archives, source mappings,
composition-normalization readiness, and explicit conversion issues.

::: perla_extract.study_extraction.nomad.to_nomad_with_report
    options:
      show_root_heading: true

## Convert to the historical reduced schema

Use `to_reduced_with_report` when conversion provenance matters. `to_reduced` is a
convenience wrapper that returns only the validated reduced cells.

::: perla_extract.study_extraction.compatibility.to_reduced_with_report
    options:
      show_root_heading: true

::: perla_extract.study_extraction.compatibility.to_reduced
    options:
      show_root_heading: true

## Plan long-document windows

`plan_evidence_windows` accepts parser-independent `EvidenceBlock` records. It guarantees that
each supplied block appears exactly once as primary evidence.

::: perla_extract.study_extraction.partitioning.plan_evidence_windows
    options:
      show_root_heading: true

## Discover and acquire papers

`run_papersbot` combines any enabled discovery sources into one incremental paper
state. Pass ordered `PdfSource` implementations when the deployment has an authorized
retrieval mechanism beyond stored Zotero attachments and open-access repositories.

::: perla_extract.papersbot.run_papersbot
    options:
      show_root_heading: true

::: perla_extract.papersbot.PdfSource
    options:
      show_root_heading: true
