# Compare Docling and GraniteDocling

This local experiment tests three conditions through the **same production
evidence adapter**. It never calls an extraction API or reads API credentials.
Production defaults remain unchanged until downstream extraction has been compared.

| Condition | Document structure | Text |
| --- | --- | --- |
| `standard` | Standard Docling | Normal Docling OCR/native-text policy |
| `granite` | GraniteDocling | Model-recognized text |
| `granite_native` | GraniteDocling | Native PDF text inside model-predicted text regions |

The native-text option replaces text blocks, not model-recognized table cells.
It is worth testing for scientific spelling. It is not
automatically safe for scanned pages: those may have no usable native text layer.
Both Granite conditions use the same 258M model revision, Transformers runtime,
2× image scale, greedy decoding, 8,192 output tokens per page and no quantization.
Tables, coordinates, context links and the native-typography supplement are
adapted identically. Source/reference hashes are checked before running.

## Run

From the repository root, install the experiment dependency in your environment:

```sh
uv pip install -e . 'docling[vlm]==2.150.0'
hf download ibm-granite/granite-docling-258M --revision 982fe3b40f2fa73c365bdb1bcacf6c81b7184bfe
```

Download standard Docling's models once with `docling-tools models download`.
Download the complete snapshot: this Docling release checks for repository
metadata as well as model weights, even offline. Downloads happen before the
timed, offline conversions. Retain the downloaded
weights and software environment when publishing results.

Use a JSON list named `papers.json` to connect each article, SI and reviewed
reference. Paths are relative to that file; `supplement` is optional:

```json
[
  {
    "paper_id": "10.1039--c5dt02388b",
    "pdf": "papers/10.1039--c5dt02388b.pdf",
    "supplement": "papers/10.1039--c5dt02388b-SI.pdf",
    "reference": "references/10.1039--c5dt02388b"
  }
]
```

Validate and seal the experiment without loading models, then execute it:

```sh
python -m examples.parsing.compare --papers papers.json --output results/parsing --plan-only
python -m examples.parsing.compare --papers papers.json --output results/parsing
```

CPU is the portable test default. Use `--device cuda` on an NVIDIA machine;
every condition receives the declared device. The Transformers VLM engine does
not support MPS in the pinned Docling version. Apple Silicon uses CPU in this
initial test; comparing MLX is a separate runtime experiment.
For an initial smoke test, use a one-paper roster
and `--page-range 4 4` **on both commands**, with a different output directory.
Page numbers are inclusive and retain original PDF numbering. `--timeout 900`
is a hard limit in seconds for each source/condition, including model loading.

Repeat the same command to continue interrupted work. Finished attempts, including
failures, are retained. Use a fresh output directory to retry failures or change
settings. Conversions are isolated and shuffled with a fixed seed; cold model-load
time and conversion time are reported separately. This is not a warm batch-throughput
benchmark.

## Interpret

- `plan.json`: frozen inputs, conditions, code hashes, versions and model revision.
- `results.csv`: every source/condition, including failures and timeouts; time,
  peak process memory, table counts, geometry, and evidence size.
- Each attempt: `docling.json`, model-facing `document.json`, `options.json`,
  `vlm-pages.json` (raw generated text, token counts and stop reasons),
  `literal-probes.json`, and `conversion.log`.

Literal probes reuse reviewed `raw_value` and `unit` strings that appear in their
existing evidence quotes. A match must occur on the cited source page. They ignore
whitespace but preserve case, signs and decimal spelling. Missing probes can reflect
equivalent formatting, and present probes do not prove correct device attribution.
The native-visible subset diagnoses loss of existing PDF text; it cannot measure
recognition of raster-only text. **These counts are not precision or recall.**

Inspect partial conversions, output-token limits, lost units/values, table headers
and their associated rows before paying for extraction. Do not pick a winner from
character counts, table counts or literal visibility alone.

To choose production defaults, run the surviving conditions on the same paper/SI
pairs with the same extraction model, prompt and frozen scorer. Compare PCE, FF,
Voc and Jsc precision/recall, device attribution, processing, completion rate and
total extraction cost. Report per-paper changes and pooled counts, retaining failed
papers in the coverage denominator. Use all available development papers for this
comparison; reserve independent papers for a later generalization claim.
