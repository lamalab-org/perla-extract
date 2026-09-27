# Test a cheaper extraction workflow

These are small, paired experiments, not changes to production defaults. They ask
which paid stages earn their cost. No result or saving is assumed in advance.
The first study tests **one versus two independent source readings**.

## Freeze the comparison before running

1. Select eight reviewed development papers using source characteristics, not model
   scores: include short and long SI, multiple specimens, population statistics,
   stability, and detailed chemistry. Categories may overlap. Record why each paper
   was selected. If fewer references are ready, label the run exploratory; do not
   substitute unreviewed seeds. Papers used to revise prompts are development data.
2. Use the same finalized reference versions, main PDFs, SI, parser, schema and
   scorer for both arms. Include the complete available SI; record explicitly when
   there is none. Inspect the correspondence of PDF hashes to the source manifest.
3. Fix model/provider IDs, provider reasoning defaults, budgets and environment before
   execution. The planner requires model IDs rather than selecting a model for you.
   `omit` leaves reasoning and temperature at provider defaults; it does not disable
   reasoning or guarantee determinism. The cheap-reader study measures the chosen
   model's deployed configuration, not architecture alone.
4. Run each arm once per paper for screening. Repeat both arms three times on two
   prespecified papers (one short, one long SI) to check variability. Do not count
   repeats as extra independent papers or choose the best response.
5. Only after development, lock a candidate and test it on a separately reviewed,
   untouched set. Eight development papers cannot establish general non-inferiority.

All studies retain targeted repair and composition/processing enrichment. No paper
figures are sent to models. Do not edit predictions after seeing the reference.
The planner records hashes and commands; the existing CLIs perform extraction and
scoring. There is no new agent framework, model router or production setting.

## Study 1: does the second source reading pay for itself?

Configuration: `examples/ablations/01-claim-readings.json`.

| Arm | Independent source readings | Everything downstream |
| --- | ---: | --- |
| Control | 2 per document/window | Unchanged |
| Treatment | 1 per document/window | Unchanged |

Hypothesis: one reading removes a substantial call/output cost while preserving
most correctly attributed facts. Risk: a claim omitted by the reader never reaches
the assembler. Inspect missing concentrations, population metrics and stability
conditions, not merely overall F1. Compare the ledger and final extraction to see
whether a recovered claim survived assembly. Claim count is not accuracy.

## Prepare commands without paid calls

From a clean checkout with PERLA installed, create a **private** cohort file:

```json
[
  {
    "paper_id": "paper-a",
    "pdf": "pdfs/paper-a.pdf",
    "supplement": "pdfs/paper-a-SI.pdf",
    "truth": "truth/v1/dev/paper-a"
  }
]
```

Paths resolve relative to this file. Use `null` for an absent separate supplement.
`truth` is an exported directory containing `ground_truth.json` and `manifest.json`.
This example is not a real roster. Keep PDFs, references and plans out of the PR.

```bash
PYTHONPATH=.:src python -m examples.ablations.prepare plan \
  --study examples/ablations/01-claim-readings.json \
  --cohort /absolute/path/to/private/cohort.json \
  --model PROVIDER/MODEL \
  --max-cost 5 \
  --output /absolute/path/to/private/study-01
```

The amount is an example **per-run spending threshold**, not a total authorization
or guaranteed hard cap. The extractor checks provider-reported spend between calls;
the last call can exceed the threshold. Approve a campaign budget separately. Eight
papers and two arms produce 16 runs before repetitions, with repairs included.

Inspect `plan.json` and then, only when spending is authorized:

```bash
bash /absolute/path/to/private/study-01/extract.sh
bash /absolute/path/to/private/study-01/score.sh
```

Each script checks recorded input hashes and the clean Git revision before starting.
It tries every planned job even when one command fails. Extraction refuses an
existing `runs` directory; prepare a new destination for a rerun rather than
overwriting predictions. Keep stdout/stderr as logs. A successful extraction CLI
exit alone is not success: inspect every run's `report.json` status.

The scripts share a parser cache but give every arm/repetition a fresh local model
response cache. Provider prefix caching may still apply: record actual cache usage
and paid cost, and do not call this a guaranteed cold-provider-cache experiment.
Execution order is shuffled with a fixed seed. Record the installed environment
alongside the plan; input hashes do not pin Python dependencies or hosted weights.

Scoring uses the full planned roster. Missing reports make aggregation fail instead
of silently shrinking the sample. A valid empty extraction scores as an empty
prediction; a missing/malformed result is reported as a run failure, not fabricated
as a successful zero-valued extraction. Resolve execution failures under the same
prespecified retry rule for both arms before claiming comparable whole-roster F1.

## Report quality and cost together

Primary outcome: paired per-paper difference in `core_facts.macro_f1`. Also show
micro counts, precision and recall separately for performance, population,
stability, composition, stack and processing. Show each paper, not just averages.
Inspect record matching issues and scientific associations blind to arm when possible.
Do not alter tolerances, references or pairings to favor a cheaper arm.

Use this **screening rule**, fixed before viewing scores: nominate a cheaper arm
only if mean paper-level F1 falls by at most 0.01, no group's pooled precision or
recall falls by more than 0.02, and it has no additional failed runs. These margins
are practical design choices, not scientifically established equivalence bounds.
Undefined groups cannot establish preservation; report their counts and coverage.
If repeats cross a margin, the result is inconclusive. Select the cheapest eligible
candidate; if none qualify, keep the control. Do not stack individually favorable
changes without a separate combined-workflow confirmation.

Report actual total provider cost (including retries/repairs), cost per attempted
paper, per-run tokens/cache usage and wall time. Keep enrichment/NOMAD checks visible:
the six core groups do not score the correctness of enriched site-ion interpretations.
Have an expert compare those outputs separately before claiming equal NOMAD quality.

The existing dataset CLI gives single-arm bootstrap intervals, **not a paired
non-inferiority test**. Compare paired paper differences and repeat ranges
descriptively for this pilot; do not infer equivalence from overlapping intervals
or a nonsignificant difference. Keep any later confirmatory statistics separate.

See [the scoring reference](evaluation.md) and
[reference preparation](review-to-benchmark.md) for the implemented scoring rules.
