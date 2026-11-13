<!-- generated-by: gsd-doc-writer -->
# PERLA Extract

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

PERLA Extract turns a photovoltaic paper and its supplementary information into
device-centered, source-linked data. It keeps device families, individual cells,
performance observations, population summaries, and stability experiments distinct.

## Start with the offline example

From a checkout of the revision you want to use, run this in the repository root
(Python 3.12, Linux/macOS):

```bash
python3.12 -m venv .venv-scoring
source .venv-scoring/bin/activate
python -m pip install -r examples/scoring/requirements.txt
PYTHONPATH=.:src python -m examples.scoring.run --output results/first-example
```

Choose a new output directory. This needs no API key, PDF, model server, Node.js,
or GPU. After installing dependencies, it runs offline. Expect
`PASS: four synthetic cases`, a `comparison.json`, and plotting CSVs in
`results/first-example/figure-data/`. The example uses deliberately edited
predictions; its difference demonstrates reporting, not a model improvement.

- [Inspect the example and its expected results](docs/workflows/scoring-example.md).
- [Run a real controlled experiment](docs/workflows/extraction-ablations.md).
- [Find the code to change and run maintainer exercises](docs/development/maintaining.md).

The [evaluation method](docs/methods/benchmark.md) explains reference construction,
matching, scientific credit and limitations. The repository includes a
[30-paper expert-reviewed reference release](data/study_extraction/ground_truth/v2/README.md)
for development comparisons, with frozen hashes and automated scorer checks.
It is not an independent held-out test set.

## Run an extraction

From the same checkout, install the full extraction dependencies:

```bash
python3.12 -m venv .venv-extraction
source .venv-extraction/bin/activate
python -m pip install -e .
export OPENAI_API_KEY="your-openai-key"

perla-extract \
  --pdf paper.pdf \
  --supplement paper_si.pdf \
  --model openai/gpt-5.2 \
  --parser pymupdf \
  --max-cost-usd 2.00 \
  --output-dir results/paper
```

Model calls use LiteLLM. The provider-prefixed model name selects the backend and its
standard credential: `openai/...` with `OPENAI_API_KEY`,
`openrouter/...` with `OPENROUTER_API_KEY`, or `anthropic/...` with
`ANTHROPIC_API_KEY`. The default calls OpenAI directly.

Use a new output folder for each attempt. A shared TOML file (`--config method.toml`)
controls stage models and limits; [open-model endpoints and Langfuse tracing](docs/workflows/open-models.md)
are optional. [Ablations](docs/workflows/extraction-ablations.md) use ordinary runs
with a small study JSON declaring the cohort and arm values, plus a CSV roster of
attempts. `perla-compare` checks that declaration and exports paired results and plotting tables.

This first run selects PyMuPDF explicitly. The default Docling parser performs
additional layout analysis and can download parser-model weights on first use.
Keep the parser fixed across the arms of a model or stage comparison.

The output includes `extraction.json`, its source evidence and retained drafts.
Saved audits show what repair and enrichment changed. If finalization removes an
unsupported optional claim, it preserves the candidate and the removed content.
NOMAD export uses reported facts and accepted interpretations without rewriting the
extraction. Use `--dry-run` to parse inputs without calling a model.

Extraction requests contain parser-produced text and tables, not rendered pages.
Figure classification is a separate workflow. Valid quotations do not prove scientific
correctness; expert review checks values, attribution and missing information.

## Documentation

The [documentation](docs/index.md), rendered with MkDocs, explains:

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

PapersBot finds papers through journal feeds, OpenAlex topic searches and an optional
Zotero group. It filters candidates, retrieves available PDFs and saves its progress
as JSON. It does not extract scientific records:

```bash
pip install 'perla-extract[papersbot]'
perla-papersbot downloaded_papers --state-dir .papersbot-state
```

Use `--feeds-file` or repeated `--feed` options to change the journal list and
`--selection-file` to change the relevance policy. `--zotero-group-id` reads a group
library and its stored PDFs; a collection can serve as a journal-club intake queue.
Zotero access is read-only. See the [PapersBot guide](docs/workflows/papersbot.md)
for group-scoped keys, internal cron setup, access records and custom PDF retrieval.

## Authors

- Sherjeel Shabih — sherjeel.shabih@hu-berlin.de
- Pepe Marquez — jose.marquez@physik.hu-berlin.de
- Kevin Jablonka — mail@kjablonka.com
- Sharat Patil — sharat.patil@physik.hu-berlin.de

When citing this software, identify the repository and exact release or commit used.
A software version alone does not identify the reference data or scoring configuration;
include their versions and hashes with evaluation results.
