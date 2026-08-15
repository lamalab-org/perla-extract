# Extracting one complete study

Use `perla-extract` when you want the devices from one paper and its SI as
source-linked records rather than one flat row of nearby values.

```bash
export OPENROUTER_API_KEY="your-openrouter-key"  # for the default backend

perla-extract \
  --pdf paper.pdf \
  --supplement paper_si.pdf \
  --parser docling \
  --model openrouter/openai/gpt-5.6-sol:exacto \
  --output-dir results/paper
```

Docling is recommended because it recovers tables, reading order, and chemical
subscripts better than plain PDF text. Install it with
`pip install 'perla-extract[docling]'`. PyMuPDF remains the lightweight fallback.

LiteLLM provides the model transport. The model name chooses the backend: the default
uses OpenRouter, while `openai/...`, `anthropic/...`, `ollama/...`, and other LiteLLM
prefixes use their matching credentials and provider. Put provider environment
variables in the process environment or `.env.local`; process values take precedence.
The explicit `:exacto` suffix preserves quality-first OpenRouter routing without
hard-coding provider behavior in the client.

## What the files contain

- `extraction.json`: the full result. Device families hold stacks, absorber
  composition, layers, and processing. Individual devices, measurements,
  population summaries, and stability tests are separate records.
- `candidates.json`: for a windowed run, the complete namespaced union before
  cross-window identity reconciliation.
- `reconciliation.json`: all proposed equivalence groups, which groups passed local
  identifier checks, and why any proposal was rejected.
- `validation.json`: exact checks that every quoted value occurs in its cited
  source block, plus checks that device and family links resolve.
- `reduced.json`: a deterministic export to the existing
  `PerovskiteSolarCells` schema.
- `reduced_conversion.json`: a source-to-row map and a list of information that
  the reduced schema cannot hold directly.
- `document.json`: the parser output with page and source locations.
- `report.json`: counts, model usage, cost, cache hits, and failures.
- `run_configuration.json`: the exact non-secret settings used for the run.

The command always writes `extraction.json`, even if the model fails. Failed raw
responses are kept under `requests/`; validated model responses and parsed PDFs
are content-addressed caches.

`grounded_facts.json` contains the conservative subset of facts whose raw value and
evidence passed local source validation. The full extraction is retained separately so
a failed evidence check lowers confidence without silently deleting model output.

## Why records are separated

A reverse scan and a forward scan may belong to the same device. A mean over 20
devices does not. A stability specimen may have no supported link to the device
whose JV curve is shown. `StudyExtraction` represents these cases explicitly, so
the exporter never silently treats a mean, champion result, and stability point
as measurements of one cell.

## Long supplementary information

The default is one full-study call because it gives the model the best view of
device identity. When the input exceeds the configured limit, the same command
splits parser blocks by document structure. Every block is primary evidence in
exactly one window, and window outputs are combined without deleting candidates.
No section-name or photovoltaic-property regular expressions decide what is
kept. Use `--mode windowed` to force this path or `--dry-run` to inspect the
planned call count before spending API tokens. A run with multiple successful
extraction windows uses one additional schema-constrained call to identify equivalent
candidates. It keeps both candidates and adds an explicit equivalence group; it never
guesses which candidate should replace the other.

## Reduced-schema compatibility

The rich-to-reduced direction is deterministic. Each performance observation,
population statistic, and stability test becomes a separate reduced cell. Data
without a faithful reduced field stays in `additional_notes` with its source IDs
and is listed in the conversion report. The reverse direction cannot be lossless:
the reduced schema does not retain device identity, multiple measurement
protocols, ordered stability checkpoints, full evidence, or candidate equivalence.
