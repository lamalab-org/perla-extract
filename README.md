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
  --max-output-tokens 40000 \
  --output-dir results/paper
```

Docling parses the paper and SI. Open `extraction.json` for records and
`report.json` for usage and issues. Omit `--supplement` when there is no SI.

| Optional pass | When to use it |
| --- | --- |
| Refinement | Test whether one source-and-draft review improves accuracy enough to justify its cost |
| Enrichment | Produce separate composition/processing interpretations for database export |

Both are off by default. [Stage and context details](docs/workflows/extraction.md)
and [other providers](docs/workflows/open-models.md) are documented separately.

## Compare two extraction choices

```bash
cp examples/experiment.toml my-experiment.toml
# Edit my-experiment.toml: model, paper/SI paths and control/treatment settings.
perla-experiment my-experiment.toml --check
perla-experiment my-experiment.toml
```

Open the generated `summary.md` first; `figure-data/` contains plotting tables.
For Astra versus cheaper models, use the [30-paper study](benchmarks/development).
[Experiment guide](docs/workflows/extraction-ablations.md).

## Find what you need

| Task | Start here |
| --- | --- |
| Try scoring without a model, PDFs or credentials | [Offline example](docs/workflows/scoring-example.md) |
| Understand stages and context limits | [Extraction workflow](docs/workflows/extraction.md) |
| Compare Docling and GraniteDocling locally | [Parser experiment](examples/parsing/README.md) |
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
