# PERLA Extract

Turn photovoltaic papers and supporting information into source-linked device,
performance, processing and stability records.

## Install

From a checkout (Python 3.12):

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

## Extract a paper

```bash
export OPENAI_API_KEY="your-openai-key"
perla-extract --pdf paper.pdf --supplement paper_si.pdf \
  --model openai/gpt-6.1-sol \
  --no-claims --no-refinement --no-targeted-repair --no-enrichment \
  --max-model-calls 1 --max-output-tokens 40000 \
  --output-dir results/paper
```

Docling is the default parser; its first use may download local parsing models.

Open `results/paper/extraction.json` for the records and `report.json` for progress,
usage and issues. Omit `--supplement` when there is no SI. Use a new output directory
for each attempt. [Open models and other providers](docs/workflows/open-models.md)
use the same workflow. This starts with one direct extraction call. Add reading,
refinement or other stages only when a controlled comparison shows they help.
The complete paper and SI must fit the input limit; the command fails before a
model call when they do not. [Context limits](docs/workflows/open-models.md#understand-context-and-resource-limits)
explain how to configure larger sources.

## Compare two extraction choices

```bash
cp examples/experiment.toml my-experiment.toml
# Edit my-experiment.toml: model, paper/SI paths and control/treatment settings.
perla-experiment my-experiment.toml --check
perla-experiment my-experiment.toml
```

One TOML file defines both arms and the papers. Start with the one-paper example;
the root `experiment.toml` lists all 30 frozen development references when you are
ready to expand. Supply the original PDFs/SIs at the configured paths.
The command handles extraction and scoring. Open the generated `summary.md` first
for reference agreement, uncertainty, run coverage and recorded costs;
`figure-data/` contains plotting tables.
[Experiment guide](docs/workflows/extraction-ablations.md).

## Find what you need

| Task | Start here |
| --- | --- |
| Try scoring without a model, PDFs or credentials | [Offline example](docs/workflows/scoring-example.md) |
| Understand stages and context limits | [Extraction workflow](docs/workflows/extraction.md) |
| Design a defensible scientific evaluation | [Validation method](docs/methods/validation-and-scale.md) |
| Change or debug the code | [Maintainer guide](docs/development/maintaining.md) |
| Review records, export data or discover papers | [Documentation](docs/index.md) |

Extraction reads PDF text and tables. Evidence citations and passing software checks
do not prove scientific correctness. The included 30-paper reference set is development
data; use independent review and untouched papers for final accuracy claims.

## Authors

- Sherjeel Shabih — sherjeel.shabih@hu-berlin.de
- Pepe Marquez — jose.marquez@physik.hu-berlin.de
- Kevin Jablonka — mail@kjablonka.com
- Sharat Patil — sharat.patil@physik.hu-berlin.de

When citing this software, identify the repository and exact release or commit used.
A software version alone does not identify the reference data or scoring configuration;
include their versions and hashes with evaluation results.
