<!-- generated-by: gsd-doc-writer -->
# Getting started

## Install

The standard installation includes the quality-first Docling parser and the explicit
PyMuPDF alternative:

```bash
pip install perla-extract
```

For a development checkout:

```bash
pip install -e '.[dev,docs]'
```

## Inspect the planned run

`--dry-run` parses and caches the documents, chooses single or windowed mode, and
writes a call estimate without contacting a model provider:

```bash
perla-extract \
  --pdf paper.pdf \
  --supplement paper_si.pdf \
  --output-dir results/paper \
  --dry-run
```

Read `results/paper/report.json` for the estimated input tokens, planned call count,
parser events, and selected mode.

## Extract

```bash
export OPENROUTER_API_KEY="your-openrouter-key"  # for the default backend

perla-extract \
  --pdf paper.pdf \
  --supplement paper_si.pdf \
  --model openrouter/openai/gpt-5.6-sol:exacto \
  --output-dir results/paper
```

Progress and periodic heartbeats go to stderr. The final report is printed as JSON on
stdout. `--json-logs` emits machine-readable logs; `--log-level DEBUG` shows parser
details.

Model transport is provider-neutral through LiteLLM. Choose a different backend with
its provider-prefixed model name and standard credential, such as `openai/...` with
`OPENAI_API_KEY` or `anthropic/...` with `ANTHROPIC_API_KEY`. The explicit `:exacto`
suffix preserves the previous quality-first routing for the default OpenRouter model.

## Read the result

| Artifact | Purpose |
| --- | --- |
| `extraction.json` | Complete rich study result, including records that need review |
| `grounded_facts.json` | Conservative subset of facts that passed local source checks |
| `validation.json` | Evidence, identifier, and relationship findings |
| `document.json` | Model-facing scientific evidence blocks with source and page locations |
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

Complete parsed documents and validated model responses use content-addressed caches.
The parser cache retains references and document furniture for provenance, while
`document.json` contains the scientific evidence view sent to the model. Its cache key
covers the source, selected backend and version, block schema, and parser implementation.
The model cache key covers the complete request, including model, schema, prompts,
reasoning, temperature, token limits, timeout, and evidence. Requests also set a fixed
seed. Provider behavior can still change, so the run configuration and source hashes are
part of the scientific record.

Parser code, schema, dependency, and source changes automatically produce distinct
cache keys. Use `--refresh-document-cache` only when you explicitly want to reparse an
otherwise identical input. Change model or request settings to produce a distinct
model-cache key.
