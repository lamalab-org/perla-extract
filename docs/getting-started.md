# Extract your first paper

## Install

From the repository root (Python 3.12):

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

The package includes PDF parsing and model-provider support. Maintainers can add
`python -m pip install -e '.[dev,docs]'`; reviewer UI tests also need Node.js 20+.
The optional [offline scoring example](workflows/scoring-example.md) has a smaller,
separate environment when you only want to try scoring.

## Check the input, then extract

```bash
perla-extract --pdf paper.pdf --supplement paper_si.pdf \
  --model openai/gpt-6.1-sol \
  --max-output-tokens 40000 \
  --output-dir results/paper-check --dry-run
```

The dry run parses the input and checks the complete request without calling a
model. Once it passes, set `OPENAI_API_KEY` in your environment and run the same
command without `--dry-run`, changing the output to `results/paper`.
Omit `--supplement` if there is no SI. Each attempt needs a new output directory.
Docling is the default parser and may download local parsing models on first use.
Select `--parser pymupdf` explicitly when testing the lightweight backend.

This baseline makes one direct extraction call. It sends the complete prepared
paper and SI; an input that exceeds the limit fails before inference. Additional
reading, refinement, repair and interpretation are separate choices to test, not
requirements for a first extraction.

For other providers, open-model servers and Langfuse, see
[models and tracing](workflows/open-models.md). Limits apply per run; inspect planned
calls before choosing a limit for a long paper.

## Read the result

| File | What it contains |
| --- | --- |
| `extraction.json` | Scientific records and citations |
| `document.json` | Parsed source text and page locations |
| `report.json` | Completion status, stage outcomes, calls and usage |
| `validation.json` | Citation, value and relationship issues |
| `run_configuration.json` | Applied settings and input fingerprints |

Progress appears in the terminal. A `partial`, `failed` or `complete_needs_review`
status needs inspection; `complete` records successful workflow checks, not expert
certification of scientific correctness. [The extraction reference](workflows/extraction.md)
explains stages, status details and retained drafts.

To compare choices, use [one experiment file](workflows/extraction-ablations.md).
To change the implementation, start with the [maintainer guide](development/maintaining.md).
