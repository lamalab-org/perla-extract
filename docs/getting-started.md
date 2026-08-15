<!-- generated-by: gsd-doc-writer -->
# Getting started

## Install

PyMuPDF is the lightweight built-in parser:

```bash
pip install perla-extract
```

Docling is optional and is intended for documents where layout and tables matter:

```bash
pip install 'perla-extract[docling]'
```

For a development checkout:

```bash
pip install -e '.[dev,docs,docling]'
```

## Inspect the planned run

`--dry-run` parses and caches the documents, chooses single or windowed mode, and
writes a call estimate without contacting OpenRouter:

```bash
perla-extract \
  --pdf paper.pdf \
  --supplement paper_si.pdf \
  --parser docling \
  --output-dir results/paper \
  --dry-run
```

Read `results/paper/report.json` for the estimated input tokens, planned call count,
parser events, and selected mode.

## Extract

```bash
export OPENROUTER_API_KEY="your-openrouter-key"

perla-extract \
  --pdf paper.pdf \
  --supplement paper_si.pdf \
  --parser docling \
  --model openai/gpt-5.6-sol \
  --output-dir results/paper
```

Progress and periodic heartbeats go to stderr. The final report is printed as JSON on
stdout. `--json-logs` emits machine-readable logs; `--log-level DEBUG` shows parser
fallback details.

## Read the result

| Artifact | Purpose |
| --- | --- |
| `extraction.json` | Complete rich study result, including records that need review |
| `grounded_facts.json` | Conservative subset of facts that passed local source checks |
| `validation.json` | Evidence, identifier, and relationship findings |
| `document.json` | Ordered parser blocks with source and page locations |
| `report.json` | Status, counts, usage, cost, cache information, and failures |
| `run_configuration.json` | Non-secret configuration and source fingerprints |
| `reduced.json` | Deterministic reduced-schema export |
| `reduced_conversion.json` | Rich-to-reduced mappings and explicit losses |

Windowed runs additionally write `window_plan.json`, successful per-window results,
`candidates.json`, and—when reconciliation is attempted—`reconciliation.json`.
Requests and preserved failure responses are stored under `requests/`.

`report.json` uses `complete` only when local validation reports no findings.
`complete_needs_review` means the model call completed but at least one local check
needs attention. `partial` means a window, reconciliation call, or conversion failed
while inspectable output was still produced; `failed` means no model call succeeded.

## Caching and repeatability

Parsed documents and validated model responses use content-addressed caches. The model
cache key covers the complete request, including model, schema, prompts, reasoning,
temperature, provider routing, and evidence. Requests also set a fixed seed. Provider
behavior can still change, so the run configuration and source hashes are part of the
scientific record.

Use `--refresh-document-cache` after changing parser behavior or when you explicitly
want to reparse. Change the model or request settings to produce a distinct model-cache
key.
