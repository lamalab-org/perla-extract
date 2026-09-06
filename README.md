<!-- generated-by: gsd-doc-writer -->
# PERLA Extract

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

PERLA Extract turns a photovoltaic paper and its supplementary information into
device-centered, source-linked data. It keeps device families, individual cells,
performance observations, population summaries, and stability experiments distinct.

```bash
pip install perla-extract
export OPENAI_API_KEY="your-openai-key"

perla-extract \
  --pdf paper.pdf \
  --supplement paper_si.pdf \
  --model openai/gpt-5.2 \
  --max-cost-usd 2.00 \
  --output-dir results/paper
```

Model calls use LiteLLM. The provider-prefixed model name selects the backend and its
standard credential—for example, `openai/...` with `OPENAI_API_KEY`,
`openrouter/...` with `OPENROUTER_API_KEY`, or `anthropic/...` with
`ANTHROPIC_API_KEY`. The default calls OpenAI directly; the extraction workflow itself
remains provider-neutral.

The extraction directory contains the refined rich result, its retained first draft,
a source-grounded ledger of experimental objects and atomic claims, a claim-coverage
audit, a bounded repair pass for audit-visible gaps, local evidence checks, and audited
conservative finalization of unsupported optional claims, and audited
composition/processing enrichment. Every finalization preserves the complete candidate
and exact removed content. Accepted interpretations feed atomic archives for
the pinned NOMAD schema without rewriting reported facts in `extraction.json`. Use
`--dry-run` to parse the documents and estimate call size without calling a model.
Every model request contains parser-produced text and tables only; the workflow does
not send rendered pages to a vision model.

## Documentation

The [documentation site](docs/index.md) explains:

- [the study and evidence model](docs/concepts/study-model.md);
- [single-call and long-supplement extraction](docs/workflows/extraction.md);
- [human ground-truth review](docs/workflows/ground-truth-review.md);
- [deterministic rich-schema evaluation](docs/workflows/evaluation.md);
- [audited composition and processing interpretation](docs/workflows/enrichment.md);
- [direct NOMAD export](docs/workflows/nomad-export.md);
- [paper discovery and Zotero journal-club intake](docs/workflows/papersbot.md); and
- [the optional reduced-schema compatibility boundary](docs/compatibility/reduced-schema.md).

Build it locally with:

```bash
pip install -e '.[docs]'
mkdocs serve
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for development and test commands.

## Discover new papers

PapersBot combines packaged journal feeds, OpenAlex topic searches, and an optional
Zotero group; applies one replaceable JSON selection policy; retrieves available PDFs;
and records incremental state as readable JSON. It is deliberately separate from
scientific data extraction:

```bash
pip install 'perla-extract[papersbot]'
perla-papersbot downloaded_papers --state-dir .papersbot-state
```

Use `--feeds-file` or repeated `--feed` options to replace the journal list and
`--selection-file` to replace the relevance policy. `--zotero-group-id` also ingests a
Zotero group, including stored PDF attachments. A configured collection can be a
journal-club-curated intake queue. Zotero access is read-only and works with a key
scoped to that group. The supported unattended deployment is an internal cron job,
with ordinary settings available through `PAPERSBOT_` environment variables. Each PDF
acquisition records its source and access basis; authorized local retrieval mechanisms
can implement the small `PdfSource` interface without adding publisher cases to the
bot.

## Authors

- Sherjeel Shabih — sherjeel.shabih@hu-berlin.de
- Pepe Marquez — jose.marquez@physik.hu-berlin.de
- Kevin Jablonka — mail@kjablonka.com
- Sharat Patil — sharat.patil@physik.hu-berlin.de

Citation information will be added with the first public release of this workflow.
