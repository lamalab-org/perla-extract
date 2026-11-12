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
configure each active deployment explicitly.

## Configure a deployment

TOML sections are `[extraction]`, `[stages.read]`, `[stages.assemble]`,
`[stages.refine]`, `[stages.repair]`, `[stages.enrich]`, and `[tracing]`.
Explicit CLI options override `[extraction]`; a stage model overrides the global
model. Paths in TOML are relative to the file, including local tokenizer files.
Unknown names and invalid setting types fail before execution.

This example is a local **reader intervention**, with other enabled stages retaining
their configured models. Replace the served name and deployment identity with your
actual values. The endpoint must support the requested JSON schema response format.

```toml
[extraction]
claim_mode = "windowed"
claim_window_input_tokens = 12000
claim_max_output_tokens = 4096
max_model_calls = 40
max_total_tokens = 250000
max_elapsed_seconds = 3600

[stages.read]
model = "openai/your-served-model"
base_url = "http://localhost:8000/v1"
api_key_env = "LOCAL_MODEL_API_KEY"
revision = "exact-weight-revision"
quantization = "your-serving-precision"
chat_template = "your-template-revision"
context_tokens = 32768
max_input_tokens = 12000
max_output_tokens = 4096
reasoning_effort = "omit"
temperature = 0.0
```

For an entirely local pipeline, configure an endpoint for every enabled stage. Repair
inherits an explicitly configured refinement deployment, including endpoint and limits,
when neither a repair deployment nor a repair-model shortcut is supplied. A partial repair limit override retains that inherited endpoint. An explicit
repair model or endpoint selects its own deployment. Enrichment uses the `enrich` deployment for its
two ordinary interpretation calls and a conditional composition retry. Disabling stages changes the method and must be recorded.

`revision`, `quantization` and `chat_template` are operator declarations used in
provenance and cache identity; they do not change or verify server weights. Configure
and verify those on the serving process. `chat_template_kwargs` and `top_k` are
forwarded only for an explicit self-hosted endpoint; `top_p` is also available.
Do not assume unsupported generation settings are ignored. See the serving engine's
[OpenAI-compatible interface](https://docs.vllm.ai/en/latest/serving/openai_compatible_server/)
and [structured-output documentation](https://docs.vllm.ai/en/latest/features/structured_outputs/).

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

`max_model_calls` counts actual provider requests including retries.
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
