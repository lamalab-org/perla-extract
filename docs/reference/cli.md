<!-- generated-by: gsd-doc-writer -->
# Command-line reference

```text
perla-extract [OPTIONS]
```

The command extracts devices, composition, processing, performance, population
statistics, and stability from one main paper and optional supplement. It prints the
final report to stdout and writes progress logs to stderr.

## Inputs and output

| Option | Default | Meaning |
| --- | --- | --- |
| `--pdf PATH` | required | Main paper PDF |
| `--supplement PATH` | none | Supporting Information PDF |
| `--output-dir DIRECTORY` | `study_extraction` | Artifact directory |
| `--env-file PATH` | `.env.local` when present | Provider environment variables to load |
| `--reduced-export` | off | Also write historical reduced-schema compatibility files |

Variables already present in the process environment take precedence over the env
file. Provider credentials are consumed by LiteLLM and are not written to
`run_configuration.json`.

## Parser and call planning

| Option | Default | Meaning |
| --- | --- | --- |
| `--parser [docling|pymupdf]` | `docling` | Explicit parser backend |
| `--mode [auto|single|windowed]` | `auto` | Complete-study or long-document execution path |
| `--single-call-max-input-tokens INTEGER` | `90000` | Estimated request limit used by auto mode |
| `--window-input-tokens INTEGER` | `60000` | Request budget used to size structural windows |
| `--dry-run` | off | Parse, plan, and write an estimate without a model call |
| `--inventory / --no-inventory` | inventory | Independently inventory records, route evidence, and audit recall |
| `--inventory-model TEXT` | `openrouter/openai/gpt-5.6-terra:exacto` | Balanced model for the compact inventory |
| `--inventory-max-output-tokens INTEGER` | `20000` | Completion limit for the value-free inventory |
| `--enrichment / --no-enrichment` | enrichment | Run the audited composition and processing interpretation stage |
| `--enrichment-model TEXT` | extraction model | Model used for the two compact enrichment calls |
| `--enrichment-max-output-tokens INTEGER` | `20000` | Completion limit for each enrichment call |
| `--refinement / --no-refinement` | refinement | Re-read the same evidence to correct the extraction draft |
| `--refinement-model TEXT` | extraction model | Optional different model for the quality pass |
| `--targeted-repair / --no-targeted-repair` | targeted repair | Retry only gaps exposed by coverage and validation audits |
| `--repair-model TEXT` | refinement or extraction model | Model for the evidence-local repair call |
| `--repair-max-output-tokens INTEGER` | `30000` | Completion limit for the bounded repair response |

Docling is the reproducible quality-first default. PyMuPDF is an explicit lightweight
alternative; parser failures never silently change backends. Complete parser results
are cached using the source, backend and dependency version, block schema, and parser
implementation. Only parser-labelled references and document furniture are withheld
from the model-facing evidence view.

## Model request

| Option | Default | Meaning |
| --- | --- | --- |
| `--model TEXT` | `openrouter/openai/gpt-5.6-sol:exacto` | LiteLLM provider-prefixed model name |
| `--reasoning-effort [omit|none|minimal|low|medium|high]` | `omit` | Reasoning setting; `omit` removes the parameter for models that do not support it |
| `--max-output-tokens INTEGER` | `80000` | Maximum completion tokens per call |
| `--temperature FLOAT` | omitted | Sampling temperature; omission leaves the provider default |
| `--timeout-seconds FLOAT` | `600` | Timeout for one live request |
| `--heartbeat-seconds FLOAT` | `20` | Progress-log interval; `0` disables heartbeats |

The model prefix selects a LiteLLM backend. For example, `openrouter/...` uses
`OPENROUTER_API_KEY`, `openai/...` uses `OPENAI_API_KEY`, and `anthropic/...` uses
`ANTHROPIC_API_KEY`. The default's explicit `:exacto` suffix preserves quality-first
OpenRouter routing without provider-specific client logic. All requests set seed `0`;
reproducibility still depends on the selected provider and model version.

Refinement is enabled in the quality-first default. It adds one detailed model call
while leaving inventory and enrichment unchanged. The draft uses a shared citation
catalog in this request, so repeated source quotations do not dominate its input. For
cost experiments, compare `--no-refinement` and cheaper `--refinement-model` settings
against frozen ground truth rather than treating lower spend as equivalent quality.

The command reports prompt tokens, completion tokens, cache hits, and provider-reported
cost for each call. A cache hit has zero new usage in the aggregate `usage` object;
the original call metadata remains under `calls[].cached_response_usage` for provenance.
Requests contain parser-produced text and tables only. No option enables rendered-page
or vision-model input in this workflow.

## Cache and logging

| Option | Default | Meaning |
| --- | --- | --- |
| `--document-cache-dir DIRECTORY` | `.perla-cache/documents` | Parsed document cache |
| `--model-cache-dir DIRECTORY` | `.perla-cache/models` | Validated response cache |
| `--refresh-document-cache` | off | Ignore and replace matching parser cache entries |
| `--log-level [DEBUG|INFO|WARNING|ERROR]` | `INFO` | Minimum stderr log level |
| `--json-logs` | off | Emit JSON log records |

Run `perla-extract --help` to see the complete installed command interface.

## PapersBot

The optional literature-discovery command has a separate dependency boundary and
does not change the extraction command:

```text
perla-papersbot [OPTIONS] [DOWNLOAD_DIR]
```

Install it with `pip install 'perla-extract[papersbot]'`. See
[Discover papers](../workflows/papersbot.md) for state, selection, and scheduled-run
behavior. RSS discovery and the policy's OpenAlex topics are enabled by default.
`--no-rss` and `--no-openalex` isolate sources; `--openalex-start-date` and
`--openalex-end-date` run an explicit `YYYY-MM-DD` backfill window.
`--zotero-group-id` adds a Zotero group as a discovery source, and
`--zotero-collection-key` limits it to one collection. `--zotero-curated` makes that
collection a human-approved extraction queue. `--zotero-save` mirrors all DOI-bearing
discovery outcomes using non-destructive namespaced tags and requires `ZOTERO_API_KEY`;
`--zotero-output-collection-key` keeps bot-created records out of the curated queue.
`--zotero-pdf-policy research-group` uploads downloaded PDFs only after verifying that
the destination group is private and has file storage enabled. Merely providing read
credentials never modifies the library.
