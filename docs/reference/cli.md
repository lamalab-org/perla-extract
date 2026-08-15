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
| `--env-file PATH` | `.env.local` when present | File from which only `OPENROUTER_API_KEY` is read |

`OPENROUTER_API_KEY` from the process environment takes precedence over the env file.
It is not written to `run_configuration.json`.

## Parser and call planning

| Option | Default | Meaning |
| --- | --- | --- |
| `--parser [auto|pymupdf|docling]` | `auto` | Parser backend; selecting Docling requires the optional dependency |
| `--mode [auto|single|windowed]` | `auto` | Complete-study or long-document execution path |
| `--single-call-max-input-tokens INTEGER` | `90000` | Estimated request limit used by auto mode |
| `--window-input-tokens INTEGER` | `60000` | Request budget used to size structural windows |
| `--dry-run` | off | Parse, plan, and write an estimate without a model call |

`auto` parser selection prefers Docling when installed and falls back to PyMuPDF if
Docling fails. Parser results are cached by source content, parser version, and backend.

## Model request

| Option | Default | Meaning |
| --- | --- | --- |
| `--model TEXT` | `openai/gpt-5.6-sol` | OpenRouter model ID |
| `--reasoning-effort [omit|none|minimal|low|medium|high]` | `medium` | OpenRouter reasoning setting; `omit` removes the parameter |
| `--max-output-tokens INTEGER` | `80000` | Maximum completion tokens per call |
| `--provider-sort [quality|throughput|latency|price|none]` | `quality` | Provider routing preference |
| `--temperature FLOAT` | omitted | Sampling temperature; omission leaves the provider default |
| `--timeout-seconds FLOAT` | `600` | Timeout for one live request |
| `--heartbeat-seconds FLOAT` | `20` | Progress-log interval; `0` disables heartbeats |

Quality routing appends OpenRouter's `:exacto` model variant unless already present.
All requests set seed `0`; reproducibility still depends on the selected provider and
model version.

## Cache and logging

| Option | Default | Meaning |
| --- | --- | --- |
| `--document-cache-dir DIRECTORY` | `.perla-cache/documents` | Parsed document cache |
| `--model-cache-dir DIRECTORY` | `.perla-cache/openrouter` | Validated response cache |
| `--refresh-document-cache` | off | Ignore and replace matching parser cache entries |
| `--log-level [DEBUG|INFO|WARNING|ERROR]` | `INFO` | Minimum stderr log level |
| `--json-logs` | off | Emit JSON log records |

Run `perla-extract --help` to see the complete installed command interface.
