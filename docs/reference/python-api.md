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

## Convert to the reduced schema

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
