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

`--dry-run` parses and caches the documents, chooses a single or windowed claim-reading
mode, and writes a call estimate without contacting a model provider:

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
  --max-cost-usd 2.00 \
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
| `grounded_values.json` | Conservative subset of reported values that passed local source checks |
| `validation.json` | Evidence, identifier, and relationship findings |
| `claim_ledger.json` | Experimental objects and atomic source claims collected before record construction |
| `claim_grounding.json` | Ledger entries admitted to or rejected from assembly guidance |
| `claim_window_plan.json` | Single-call or section-aware claim-reading plan |
| `claim_coverage_audit.json` | Covered, possible, unmatched, context, and unsupported-record findings |
| `targeted_repair.json` | Evidence-local repair worklist, proposed-record counts, quality gates, and decision |
| `citation_repairs.json` | Audited non-contiguous-quote and unique-pointer repairs |
| `document.json` | Model-facing scientific evidence blocks with source and page locations |
| `report.json` | Status, counts, usage, cost, cache information, and failures |
| `run_configuration.json` | Non-secret configuration and source fingerprints |
| `nomad/*.archive.json` | One standalone NOMAD archive per atomic extracted record |
| `nomad/manifest.json` | NOMAD target pin, record mappings, and conversion issues |
| `composition_projection.json` | Formula/site-ion normalization review queue |
| `enrichment.json` | Absorber-scoped composition and processing proposals with deterministic decisions |
| `draft_extraction.json` | First complete-study result retained before the default quality pass |
| `refinement_audit.json` | Record IDs added, removed, or changed by the complete-study quality pass |
| `quality_comparison.json` | Draft-versus-final validation and semantic claim-coverage counts |
| `reduced.json` | Optional historical export when `--reduced-export` is passed |

When claim collection is windowed, every window still contributes to one combined
`claim_ledger.json`; final study assembly remains global. Requests and preserved
failure responses are stored under `requests/`.

`report.json` uses `complete` only when local validation and claim coverage report
no findings.
`complete_needs_review` means the model call completed but at least one local check
needs attention. `partial` means a claim-reading window, optional call, or conversion failed
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

`run_configuration.json` records a small integer `schema_version` for intentional
compatibility breaks and automatically computed SHA-256 fingerprints for the generated
Pydantic schema, all model prompts, and the exact deterministic evidence-span catalog.
A schema, prompt, or citable-evidence change therefore changes provenance and cache
identity without relying on a date string or a manual patch bump.
Version 4 separates layer constituents and source-backed physical form and changes the
model transport from generated quotations to deterministic evidence-span IDs. Older
JSON remains readable because the new layer fields default to empty or
`not_reported`; it is not exactly comparable to a version-4 extraction until those
new fields have been reviewed or the seed has been regenerated.
