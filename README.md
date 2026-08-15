# PERLA Extract

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

PERLA Extract turns a photovoltaic paper and its supplementary information into
device-centered, source-linked data. It keeps device families, individual cells,
performance observations, population statistics, and stability experiments distinct.
The rich result can also be converted deterministically to the reduced
[PERLA](https://fairmat-nfdi.github.io/perla/) schema.

The current production workflow is intentionally simple:

1. Parse the paper and SI into ordered, page-addressable evidence blocks.
2. Ask one frontier model to extract the complete study when it fits in context.
3. Use structure-preserving evidence windows only when the input is too long.
4. Reconcile cross-window identity through explicit equivalence links without deleting
   candidates.
5. Validate every source quote and link locally with Pydantic models.
6. Write both the rich result and a reduced-schema compatibility export.

No property-specific regular expressions decide which scientific values to extract.

## Installation

```bash
pip install perla-extract
```

PyMuPDF is the lightweight built-in parser. Docling generally preserves scientific
tables and layout better and can be installed as an optional dependency:

```bash
pip install 'perla-extract[docling]'
```

For development:

```bash
pip install -e '.[dev,docling]'
```

The literature-discovery bot is kept as a separate optional installation so feed and
HTTP dependencies do not enlarge the extractor runtime:

```bash
pip install 'perla-extract[papersbot]'
```

## Extract a study

```bash
export OPENROUTER_API_KEY="your-openrouter-key"  # for the default backend

perla-extract \
  --pdf paper.pdf \
  --supplement paper_si.pdf \
  --parser docling \
  --model openrouter/openai/gpt-5.6-sol:exacto \
  --output-dir results/paper
```

The command prints its final report as JSON on stdout. Progress logs go to stderr,
including a heartbeat during slow model calls. Use `--json-logs` for structured log
records and `--log-level DEBUG` to inspect parser fallbacks. Run
`perla-extract --help` for all options.

For a cost-free parse and call-size estimate:

```bash
perla-extract --pdf paper.pdf --supplement paper_si.pdf --dry-run
```

Model and document responses are cached by their complete inputs. The default
workflow sends the complete study to one frontier model when it fits. It does not
replace the resulting records with lower-context expansion calls, which experiments
showed could reduce scientific recall.

Model calls use LiteLLM. Select another backend with its provider-prefixed model name
and corresponding environment variable—for example, an `openai/...` model with
`OPENAI_API_KEY` or an `anthropic/...` model with `ANTHROPIC_API_KEY`. `.env.local` is
loaded when present, and existing process variables take precedence. The explicit
`:exacto` suffix preserves the previous quality-first OpenRouter default without
provider-specific routing code.

## Output

- `extraction.json` — the full device-centered result.
- `candidates.json` — the lossless pre-reconciliation union for windowed runs.
- `reconciliation.json` — proposed, accepted, and rejected cross-window identity links.
- `grounded_facts.json` — facts that passed local source checks.
- `validation.json` — evidence, identity, and link validation findings.
- `reduced.json` — deterministic export to the existing reduced PERLA schema.
- `reduced_conversion.json` — source-to-row mapping and explicit conversion losses.
- `document.json` — parser blocks with page and source locations.
- `report.json` — status, record counts, token usage, cost, cache hits, and failures.
- `run_configuration.json` — the complete non-secret run configuration.

Even a failed model run writes an inspectable `extraction.json` and `report.json`.
Raw failed responses remain under `requests/`. Reconciliation never deletes or
heuristically merges a candidate; accepted equivalence groups are included in the rich
result and conversion losses are explicit in `reduced_conversion.json`.

See [the study-extraction guide](docs/study-extraction.md) for the schema contract,
long-SI behavior, and reduced-schema mapping.

## Python API

```python
from perla_extract.study_extraction.cli import extract_study

report = extract_study(
    pdf="paper.pdf",
    supplement="paper_si.pdf",
    output_dir="results/paper",
)
```

Historical evaluation datasets remain under `src/perla_extract/data/` as repository
research assets. They are not installed with the production package.

## Discover new papers

PapersBot checks the packaged journal feeds, applies a replaceable JSON selection
policy, resolves open-access PDF links, and records incremental state as readable
JSON. It is deliberately separate from scientific data extraction:

```bash
perla-papersbot downloaded_papers --state-dir .papersbot-state
```

Use `--feeds-file` or repeated `--feed` options to replace the journal list and
`--selection-file` to replace the relevance policy. The daily GitHub workflow caches
the state and publishes newly downloaded PDFs as a run artifact.

## Authors

- Sherjeel Shabih — sherjeel.shabih@hu-berlin.de
- Pepe Marquez — jose.marquez@physik.hu-berlin.de
- Kevin Jablonka — mail@kjablonka.com
- Sharat Patil — sharat.patil@physik.hu-berlin.de

## Citation

Citation information will be added with the first public release of this workflow.
