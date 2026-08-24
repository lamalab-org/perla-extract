<!-- generated-by: gsd-doc-writer -->
# PERLA Extract

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

PERLA Extract turns a photovoltaic paper and its supplementary information into
device-centered, source-linked data. It keeps device families, individual cells,
performance observations, population summaries, and stability experiments distinct.

```bash
pip install perla-extract
export OPENROUTER_API_KEY="your-openrouter-key"  # for the default backend

perla-extract \
  --pdf paper.pdf \
  --supplement paper_si.pdf \
  --model openrouter/openai/gpt-5.6-sol:exacto \
  --output-dir results/paper
```

Model calls use LiteLLM. The provider-prefixed model name selects the backend and its
standard credential—for example, `openai/...` with `OPENAI_API_KEY` or
`anthropic/...` with `ANTHROPIC_API_KEY`. The default uses OpenRouter with an explicit
quality-first `:exacto` suffix, but the extraction workflow itself is provider-neutral.

The extraction directory contains the refined rich result, its retained first draft,
an independent source-grounded record inventory and coverage audit, a bounded repair
pass for audit-visible gaps, local evidence checks, and a separate audited
composition/processing enrichment. Accepted interpretations feed atomic archives for
the pinned NOMAD schema without rewriting reported facts in `extraction.json`. Use
`--dry-run` to parse the documents and estimate call size without calling a model.
Every model request contains parser-produced text and tables only; the workflow does
not send rendered pages to a vision model.

## Documentation

The [documentation site](docs/index.md) explains:

- [the study and evidence model](docs/concepts/study-model.md);
- [single-call and long-supplement extraction](docs/workflows/extraction.md);
- [human ground-truth review](docs/workflows/ground-truth-review.md);
- [audited composition and processing interpretation](docs/workflows/enrichment.md);
- [direct NOMAD export](docs/workflows/nomad-export.md); and
- [the optional reduced-schema compatibility boundary](docs/compatibility/reduced-schema.md).

Build it locally with:

```bash
pip install -e '.[docs]'
mkdocs serve
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for development and test commands.

## Discover new papers

PapersBot checks the packaged journal feeds, applies a replaceable JSON selection
policy, resolves open-access PDF links, and records incremental state as readable
JSON. It is deliberately separate from scientific data extraction:

```bash
pip install 'perla-extract[papersbot]'
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

Citation information will be added with the first public release of this workflow.
