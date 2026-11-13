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
| `--config PATH` | none | TOML method and stage deployments; explicit CLI options override file values |
| `--pdf PATH` | CLI or TOML required | Main paper PDF |
| `--supplement PATH` | none | Supporting Information PDF |
| `--output-dir DIRECTORY` | `study_extraction` | New or empty artifact directory |
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
| `--claim-recall-passes INTEGER` | `2` | Independent complete readings of every claim document/window |
| `--assembly-max-input-tokens INTEGER` | `180000` | Default input cap for assembly, revision, repair and enrichment; stage limits can lower it |
| `--enrichment / --no-enrichment` | enrichment | Run the audited composition and processing interpretation stage |
| `--enrichment-model TEXT` | extraction model | Model used for interpretation calls, including a conditional composition retry |
| `--enrichment-max-output-tokens INTEGER` | `20000` | Completion limit for each enrichment call |
| `--refinement / --no-refinement` | refinement | Re-read the same evidence to correct the extraction draft |
| `--refinement-model TEXT` | extraction model | Optional different model for the quality pass |
| `--targeted-repair / --no-targeted-repair` | targeted repair | Retry only gaps exposed by coverage and validation audits |
| `--repair-model TEXT` | refinement or extraction model | Model for the evidence-local repair call |
| `--repair-max-output-tokens INTEGER` | `30000` | Completion limit for the bounded repair response |

Docling is the default parser. PyMuPDF is an explicit lightweight
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
| `--reasoning-effort [omit|none|minimal|low|medium|high|xhigh]` | `omit` | Reasoning setting; `omit` removes the parameter for models that do not support it |
| `--max-output-tokens INTEGER` | `80000` | Completion allowance for assembly and full revision; other stages have separate limits |
| `--max-model-calls INTEGER` | omitted | Maximum provider requests, including retries and validation repair |
| `--max-cost-usd FLOAT` | omitted | Stop before the next call after adapter-reported cost reaches this limit |
| `--max-total-tokens INTEGER` | omitted | Token budget for starting further provider requests |
| `--max-elapsed-seconds FLOAT` | omitted | Elapsed-time budget for starting further provider requests |
| `--temperature FLOAT` | omitted | Sampling temperature; omission leaves the provider default |
| `--timeout-seconds FLOAT` | `600` | Timeout for one live request |
| `--heartbeat-seconds FLOAT` | `20` | Progress-log interval; `0` disables heartbeats |

The model prefix selects a LiteLLM backend. For example, `openai/...` uses
`OPENAI_API_KEY`, `openrouter/...` uses `OPENROUTER_API_KEY`, and `anthropic/...` uses
`ANTHROPIC_API_KEY`. The default calls OpenAI directly without provider-specific client
logic. All requests set seed `0`;
reproducibility still depends on the selected provider and model version.

Refinement is enabled by default and adds a global reconciliation call. Use
`--no-refinement` to disable it or `--refinement-model` to choose its model.
The request uses a shared citation catalog to avoid repeating source quotations.

OpenRouter can also route an eligible model through a provider key stored in its BYOK
settings. The local process still receives only `OPENROUTER_API_KEY`; do not place the
upstream OpenAI key in a project environment file as well. Pin the BYOK credential if
shared OpenRouter capacity must never be used, then verify `is_byok` in OpenRouter's
generation record with a small run before starting a costly cohort. BYOK configuration
and fallback behavior are documented by
[OpenRouter](https://openrouter.ai/docs/guides/overview/auth/byok).

The command reports prompt tokens, completion tokens, cache hits, and adapter-reported
cost for each call. Its provenance is saved; an adapter amount may be estimated and
is not verified against a provider invoice. Use `--max-model-calls` for a hard request limit and
`--max-cost-usd` to stop before another request after known spend reaches the limit.
Because providers report cost only after a response, one response can cross the monetary
threshold; if cost is omitted, a configured monetary limit fails closed before the next
request. A cache hit has zero new usage in the aggregate `usage` object;
the original call metadata remains under `calls[].cached_response_usage` for provenance.

## Revalidate cached runs

```bash
perla-extract-revalidate --runs-dir results/review-v1
```

This reapplies current deterministic evidence checks without contacting a model.
Each run gets a fresh `revalidation-0001` (or next numbered) directory containing
`validation.json`, `grounded_values.json`, and a derived report. The original sealed
report and paid-work accounting remain unchanged.
Requests contain parser-produced text and tables only. No option enables rendered-page
or vision-model input in this workflow.

## Evaluation

```text
perla-evaluate --truth PATH --prediction PATH [OPTIONS]
```

`--truth` accepts either a frozen benchmark directory or a rich truth JSON;
`--prediction` accepts an extraction run directory or `extraction.json`. Frozen
directories receive schema- and content-hash verification. The record-matching
threshold and numeric tolerances are explicit options and are written into the output
report. See [Evaluate an extraction](../workflows/evaluation.md).

To explain specific scored fields, supply repeated `--explain-truth /json/path`
or `--explain-prediction /json/path` options with `--explanation-output FILE`.
`--explain-view` selects `strict` (default), `value`, or `attribution`;
`--explanation-candidates` limits displayed candidates to 1–20 (default 5).
The explanation uses the same comparisons and assignments as the score and writes
a separate artifact. Both output paths must be new. See the
[offline score explanation example](../development/scoring.md).

Use repeated `--report` options with `perla-evaluate-dataset` to aggregate compatible
per-paper reports with micro counts, macro rates, and paper-bootstrap intervals.
Add `--release PATH/TO/release.json` for a frozen benchmark: aggregation then requires
exactly that paper roster, split and reference hashes, and saves the release and
input report hashes with the scores. Without this option, completeness is not checked.
Numeric absolute tolerance is in canonical base units, not the reference's display
unit. Dataset reports retain `--bootstrap-samples` and `--seed`, explain unavailable
intervals, and distinguish complete from partial or unknown cost accounting.

`--operation-aliases FILE` replaces the scorer's operation equivalence map with a
frozen JSON object. Both evaluation commands accept `--fail-on-scoring-issues` to
save diagnostics and then exit nonzero when attribution ambiguity needs inspection.
This gate concerns matching, not scientific certification or a minimum F1.

## Cache and logging

| Option | Default | Meaning |
| --- | --- | --- |
| `--document-cache-dir DIRECTORY` | `.perla-cache/documents` | Parsed document cache |
| `--model-cache-dir DIRECTORY` | `.perla-cache/models` | Validated response cache |
| `--refresh-document-cache` | off | Ignore and replace matching parser cache entries |
| `--log-level [DEBUG|INFO|WARNING|ERROR]` | `INFO` | Minimum stderr log level |
| `--json-logs` | off | Emit JSON log records |
| `--langfuse / --no-langfuse` | off | Export the local run event stream through the optional Langfuse adapter |

Every attempt writes `events.jsonl`; run output directories are immutable. TOML
stage deployments expose endpoint, revision, tokenizer, context and decoding controls.
See [open models and tracing](../workflows/open-models.md) for a complete example.

## Run an experiment

```bash
perla-experiment experiment.toml --check
perla-experiment experiment.toml
```

The single TOML defines shared `[extraction]` settings, `[control]` and `[treatment]`
overrides, and `[[papers]]` with `pdf`, optional `supplement`, and `reference` paths.
Each paper block explicitly links the main article, its SI, and the reviewed
ground-truth directory for both sources. Filenames need not share a stem. The
reference manifest supplies the paper ID; source hashes verify the file set,
while the TOML fields assign the main/SI roles.
Paths are relative to the TOML file. `output` names a new results directory;
`repeats` defaults to 1 and `order_seed` to 0. Shared `[stages]` and `[tracing]` use
the extraction configuration format; `[control.stages.NAME]` or
`[treatment.stages.NAME]` override a stage in one arm.

`--check` prints the validated paper/SI/reference mapping and checks every source/reference hash and setting without parsing PDFs,
creating outputs or making model calls. Running freezes the intended roster before
extraction, assigns independent response caches, scores each usable result and
writes `summary.md` (read first), `comparison.json`, and figure data. Failed attempts remain visible and prevent a complete
quality result. Existing output directories are refused; there is no automatic
campaign retry or resume. See [Run an experiment](../workflows/extraction-ablations.md).

## Compare externally executed runs

```bash
perla-compare runs.csv --study study.json --output comparison.json --tables-dir figure-data
```

The roster requires `paper_id,arm,repeat,run_dir`; paths are relative to the CSV.
An optional `role` column distinguishes `paired` scores from additional
`accounting_only` attempts, whose repeat may be blank.
Each run is scored into its own `evaluation.json`. The comparison verifies sealed
configuration, output and accounting identities, then computes paired paper differences.
The required study JSON declares `paper_ids`, the positive integer `repeats`, and
`factors` mapping each changed setting to its expected `control` and `treatment`
values. Missing/extra pairs, wrong arm values, undeclared differences and settings
that drift across papers/repeats within an arm block the quality aggregate.
The report and figure metadata retain the study contents and hash.
Missing quality and missing cost remain separate. See the [artifact reference](experiment-artifacts.md).

Run `perla-extract --help` to see the complete installed command interface.

## PapersBot

The literature-discovery command is separate from the extraction command. Its feed
and HTTP dependencies are installed through the `papersbot` dependency group:

```text
perla-papersbot [OPTIONS] [DOWNLOAD_DIR]
```

Install it with `pip install 'perla-extract[papersbot]'`. See
[Discover papers](../workflows/papersbot.md) for state, selection, and scheduled-run
behavior. RSS discovery and the policy's OpenAlex topics are enabled by default.
`--no-rss` and `--no-openalex` isolate sources; `--openalex-start-date` and
`--openalex-end-date` run an explicit `YYYY-MM-DD` backfill window.
`--openalex-api-key` enables the larger authenticated OpenAlex budget, while
`--request-retries` controls bounded retries for GET rate limits and transient server
errors.
`--zotero-group-id` adds a Zotero group as a discovery source, and
`--zotero-collection-key` limits it to one collection using its collection identifier, not its display name
or the API credential. `--zotero-curated` makes that collection a
human-approved extraction queue. Zotero access is read-only; `ZOTERO_API_KEY` is
needed only when the selected group library is not publicly readable. The key is never
written to bot artifacts.

For unattended use, ordinary options use the `PAPERSBOT_` environment prefix:
`PAPERSBOT_DOWNLOAD_DIR`, `PAPERSBOT_STATE_DIR`, `PAPERSBOT_RSS`,
`PAPERSBOT_OPENALEX`, `PAPERSBOT_MAX_ATTEMPTS`, `PAPERSBOT_REQUEST_RETRIES`,
`PAPERSBOT_FAIL_ON_PARTIAL`, and `PAPERSBOT_LOG_FILE` are direct
examples. Zotero credentials retain the explicit `ZOTERO_` names documented in the
workflow guide. `OPENALEX_API_KEY` and `ZOTERO_API_KEY` are never written to bot
artifacts. Command-line values take precedence.
