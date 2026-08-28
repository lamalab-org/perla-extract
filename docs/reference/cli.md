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
| `--claim-mode [auto|single|windowed]` | `auto` | One-call or windowed source-claim collection |
| `--single-call-max-input-tokens INTEGER` | `90000` | Claim-input estimate at which auto mode uses windows |
| `--claim-window-input-tokens INTEGER` | `60000` | Request budget used to size claim-reading windows |
| `--dry-run` | off | Parse, plan, and write an estimate without a model call |
| `--claims / --no-claims` | claims | Collect and ground objects and atomic source claims before assembly |
| `--claim-model TEXT` | extraction model | LiteLLM model used for claim collection |
| `--claim-max-output-tokens INTEGER` | `30000` | Completion limit for each claim-collection call |
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

Only claim collection uses windows. The grounded ledgers from all windows are combined
before a single global study-assembly call, so the final schema is never constructed
independently per window and merged afterward.

## Model request

| Option | Default | Meaning |
| --- | --- | --- |
| `--model TEXT` | `openai/gpt-5.2` | LiteLLM provider-prefixed model name |
| `--reasoning-effort [omit|none|minimal|low|medium|high]` | `omit` | Reasoning setting; `omit` removes the parameter for models that do not support it |
| `--max-output-tokens INTEGER` | `80000` | Maximum completion tokens per call |
| `--max-model-calls INTEGER` | omitted | Maximum provider requests, including retries and validation repair |
| `--max-cost-usd FLOAT` | omitted | Stop before the next call after provider-reported spend reaches this limit |
| `--temperature FLOAT` | omitted | Sampling temperature; omission leaves the provider default |
| `--timeout-seconds FLOAT` | `600` | Timeout for one live request |
| `--heartbeat-seconds FLOAT` | `20` | Progress-log interval; `0` disables heartbeats |

The model prefix selects a LiteLLM backend. For example, `openai/...` uses
`OPENAI_API_KEY`, `openrouter/...` uses `OPENROUTER_API_KEY`, and `anthropic/...` uses
`ANTHROPIC_API_KEY`. The default calls OpenAI directly without provider-specific client
logic. All requests set seed `0`;
reproducibility still depends on the selected provider and model version.

Refinement is enabled in the quality-first default. It adds one detailed model call
while leaving claim collection and enrichment unchanged. The draft uses a shared citation
catalog in this request, so repeated source quotations do not dominate its input. For
cost experiments, compare `--no-refinement` and cheaper `--refinement-model` settings
against frozen ground truth rather than treating lower spend as equivalent quality.

The command reports prompt tokens, completion tokens, cache hits, and provider-reported
cost for each call. Use `--max-model-calls` for a hard request limit and
`--max-cost-usd` to stop before another request after known spend reaches the limit.
Because providers report cost only after a response, one response can cross the monetary
threshold; if cost is omitted, a configured monetary limit fails closed before the next
request. A cache hit has zero new usage in the aggregate `usage` object;
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
`--zotero-collection-key` limits it to one collection using Zotero's API key for that
collection, not its display name. `--zotero-curated` makes that collection a
human-approved extraction queue. `--zotero-save` mirrors DOI-bearing discoveries and
updates namespaced status tags; it requires `ZOTERO_API_KEY`.
`--zotero-output-collection-key` keeps bot-created records out of the curated queue.
`--zotero-pdf-policy research-group` uploads downloaded PDFs only after verifying that
the destination group is private and has file storage enabled. Merely providing read
credentials never modifies the library.

For unattended use, ordinary options use the `PAPERSBOT_` environment prefix:
`PAPERSBOT_DOWNLOAD_DIR`, `PAPERSBOT_STATE_DIR`, `PAPERSBOT_RSS`,
`PAPERSBOT_OPENALEX`, `PAPERSBOT_MAX_ATTEMPTS`, and `PAPERSBOT_LOG_FILE` are direct
examples. Zotero credentials retain the explicit `ZOTERO_` names documented in the
workflow guide. Command-line values take precedence.
