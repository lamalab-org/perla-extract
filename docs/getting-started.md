<!-- generated-by: gsd-doc-writer -->
# Getting started

## First, check the workflow without a model

The [offline example](workflows/scoring-example.md) creates its own evidence,
reviewed reference, four scored candidates, paired comparison and figure tables.
Start there to check the reporting path without credentials, PDF parsers or a GPU.
Then follow the extraction steps below and the
[controlled experiment guide](workflows/extraction-ablations.md).
The [maintainer guide](development/maintaining.md) maps these operations to code.

## Install

The standard installation includes the Docling parser and the explicit
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
  --output-dir results/paper-plan \
  --dry-run
```

Read `results/paper-plan/report.json` for the estimated input tokens, planned call count,
parser events, and selected mode. Every attempt, including a dry run, requires a new
or empty output directory. The live example below writes to `results/paper` instead.

## Extract

```bash
export OPENAI_API_KEY="your-openai-key"

perla-extract \
  --pdf paper.pdf \
  --supplement paper_si.pdf \
  --model openai/gpt-5.2 \
  --max-cost-usd 2.00 \
  --output-dir results/paper
```

Progress and periodic heartbeats go to stderr. The final report is printed as JSON on
stdout. `--json-logs` emits machine-readable logs; `--log-level DEBUG` shows parser
details.

Model transport is provider-neutral through LiteLLM. The default calls OpenAI directly
with `OPENAI_API_KEY`. Choose another backend with its provider-prefixed model name and
standard credential, such as `openrouter/...` with `OPENROUTER_API_KEY` or
`anthropic/...` with `ANTHROPIC_API_KEY`.

## Read the result

| Artifact | Start here to… |
| --- | --- |
| `extraction.json` | Inspect device records, measurements and their citations |
| `document.json` | Read the parsed source text and page locations |
| `validation.json` | Find citation, value and relationship issues |
| `report.json` | Check completion status, calls, tokens and reported cost |
| `run_configuration.json` | Identify the settings and source fingerprints |

Additional audits and exports are listed in the
[artifact reference](workflows/extraction.md).

When claim collection is windowed, every window still contributes to one combined
`claim_ledger.json`; final study assembly remains global. Requests and preserved
failure responses are stored under `requests/`.

`report.json` uses `complete` when assembly succeeded and the enabled integrity
and interpretation checks have no outstanding findings. `complete_needs_review`
means validation, reader-note coverage, or optional interpretation needs attention.
An interpretation failure can therefore appear under this status.
`partial` means assembly succeeded but reading, refinement, or an export had errors.
`failed` means assembly did not complete or execution stopped with an exception;
a reading call may still have succeeded and incurred cost. `interrupted` records
a handled cancellation. Inspect `stages`, `errors`, and `usage` for the details;
these statuses do not certify scientific correctness.

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
Older outputs may remain readable after a schema change without containing all the
fields supported by the new schema. Preserve their original provenance when importing
or evaluating them.
