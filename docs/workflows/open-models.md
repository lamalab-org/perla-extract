# Run with open models and trace decisions

The extraction stages can use different LiteLLM deployments. Begin by testing a local
reader against a fixed assembly model, then test assembly and revision separately.
The code does not silently substitute a different model when a deployment fails.
A smaller schema, grounded source windows and evidence-local repair reduce unnecessary
work; they do not establish that a particular model is scientifically adequate.

For a fully local starting point, edit `examples/open-models/direct-local.toml` to
match your server. It enables only direct study assembly; this is an explicit baseline
for experimentation, not a measured replacement for the default method. Check it
without a model call first:

```bash
perla-extract --config examples/open-models/direct-local.toml --pdf paper.pdf \
  --output-dir runs/local-plan --dry-run
```

Use a different output directory for the live attempt. Add stages one at a time and
configure the default deployment before adding stage overrides.

## Configure a deployment

Put shared model settings in `[stages.default]`. Use a named stage only when it
needs different settings. `[extraction]` contains pipeline switches, parsing and
run budgets; `[tracing]` enables Langfuse. Paths are relative to the TOML file.

This uses OpenRouter by default and Blablador for reading:

```toml
[stages.default]
model = "openrouter/<organization>/<model>"

[stages.read]
model = "openai/<Blablador-served-model-id>"
base_url = "https://api.blablador.fz-juelich.de/v1"
api_key_env = "BLABLADOR_API_KEY"
reasoning_effort = "omit"
max_output_tokens = 4096
```

To use Blablador throughout, put its settings in `[stages.default]` instead. Store
keys in `.env.local`; `api_key_env` names the variable, never its value. Here
`openai/` selects the compatible API adapter; requests go to `base_url`.

The stages are `read`, `assemble`, `refine`, `repair`, and `enrich`. All inherit the
default, including repair. A stage table containing only limits or generation
controls adjusts the default deployment. For example, `[stages.read]` with just
`max_output_tokens = 16000` keeps the shared model and connection.

**Specifying `model` selects a separate deployment.** Supply its endpoint and key
variable alongside it when needed; settings from the default model are not carried
across. An endpoint or key override without a model is rejected. The same rule
applies to model changes in experiment arms and the CLI's `--model` shortcut.
This prevents one provider's credentials, tokenizer or weight identity from being
silently applied to another. There is no special refinement-to-repair inheritance.

`--model`, `--base-url`, `--api-key-env`, `--reasoning-effort`, `--temperature`, and
`--max-output-tokens` configure the default deployment. Stage overrides stay in TOML.
The old `claim_model`, `refinement_model`, `repair_model`, `enrichment_model` and
per-stage output-limit shortcuts have been removed; configuration errors point to
their replacement under `stages`.

Without an output override, the defaults are 30,000 tokens for reading and repair,
80,000 for assembly and refinement, and 20,000 for enrichment. Set limits suitable
for the served model. `context_tokens`, `max_input_tokens`, `max_output_tokens`,
`temperature`, `reasoning_effort`, `top_p`, and `tokenizer` use the same names on
the default and stage tables. `top_k` and `chat_template_kwargs` require an explicit
endpoint. Unsupported controls fail rather than being silently discarded.

`revision`, `quantization` and `chat_template` record operator declarations for
provenance and cache identity. They do not change or verify the server's weights.
See your serving engine's [API](https://docs.vllm.ai/en/latest/serving/openai_compatible_server/)
and [structured-output support](https://docs.vllm.ai/en/latest/features/structured_outputs/).

### Choose a Blablador model

Obtain a key using [Blablador's instructions](https://sdlaml.pages.jsc.fz-juelich.de/ai/guides/blablador_api_access/).
Read the authenticated `https://api.blablador.fz-juelich.de/v1/models` catalog before
choosing a model: [aliases can change their underlying model](https://sdlaml.pages.jsc.fz-juelich.de/ai/guides/blablador_models/).
Save that catalog with the experiment, prefer a model-specific served ID, and record
the advertised root and context limit. This does not verify the weight revision.
Test a small schema-constrained response before running papers.

Use request, token and elapsed-time limits. If the endpoint omits dollar cost,
`max_cost_usd` prevents the next request; omit that limit when cost cannot be tracked.
Missing cost remains unknown in the results.

## Understand context and resource limits

Planning and live requests use the same serializer for messages and the response
schema. Schema, instructions, evidence, draft and validation-retry turns all count.
The default UTF-8-byte/3 estimate plus framing allowance is a heuristic, not the
provider's tokenizer. Set `tokenizer` to a local `tokenizer.json` for tokenization
with the optional `tokenizers` package, or `"utf8_bytes"` for a loose text bound.
No tokenizer is downloaded automatically. Provider chat/schema framing can still
differ; keep headroom and inspect the saved component counts.

Source windows must fit the reader allowance. Oversized indivisible evidence fails
visibly instead of disappearing. Global assembly and revision remain whole-study
operations and may be the limiting stages for smaller models. Do not claim a fully
small-context pipeline merely because the reading stage is windowed.

`max_model_calls` limits request reservations, including retries. A local write
failure before dispatch can consume a reservation without creating a provider
attempt; use `usage.provider_attempts` to count attempted provider work.
`max_total_tokens` reserves estimated input plus the output allowance before a request.
Provider usage updates the observed total afterward; an observed overage is reported
and the valid response is retained. `max_elapsed_seconds` also limits the timeout of
new HTTP requests to the remaining time; synchronous parsing is not forcibly interrupted.
These are explicit best-effort resource limits, not guarantees about provider billing. Adapter-reported cost is
recorded with its provenance; an unspecified adapter amount is not verified billing. Missing cost is not zero; self-hosted deployments
should primarily compare tokens, latency and independently measured GPU resources.
Cache identity includes the effective deployment and generation settings. Changing
an endpoint, revision or template cannot silently reuse a previous deployment's answer.

## Inspect locally or with Langfuse

Every run writes `events.jsonl` with a shared run ID, parent stages, logical calls,
provider attempts, cache outcomes, limits and candidate decisions. A logical call
can contain multiple paid attempts. Read `report.json` for totals and the request
artifacts for exact model inputs. Interrupted or failed work remains visible.

Optional Langfuse export uses the tested Python SDK v4 compatibility range:

```bash
pip install 'perla-extract[tracing]'
export LANGFUSE_PUBLIC_KEY='your-public-key'
export LANGFUSE_SECRET_KEY='your-secret-key'
export LANGFUSE_BASE_URL='https://your-langfuse-host'
perla-extract --config method.toml --pdf paper.pdf --output-dir runs/traced --langfuse
```

Alternatively set `[tracing] langfuse = true`. The adapter creates a run trace with
nested stages, calls and generations from the same local event stream. It sends
execution metadata and artifact references; full source and request payloads remain
in local artifacts. Do not enable another automatic callback for the same calls.
Telemetry failure is recorded locally and does not alter the scientific result.
The dependency intentionally pins SDK v4; consult Langfuse's
[SDK and server compatibility guidance](https://langfuse.com/docs/observability/sdk/overview)
when upgrading. A live server connection is separate from offline adapter tests.
