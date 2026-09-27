# Test a cheaper extraction workflow

These are small, paired experiments, not changes to production defaults. They ask
which paid stages earn their cost. No result or saving is assumed in advance.
The studies test repeated reading, global refinement and the claim ledger separately.

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

## Study 2: does global refinement improve the final result?

Configuration: `examples/ablations/02-global-refinement.json`. Substitute this file
in the preparation command below and choose a new output directory.

| Arm | Global study rewrite | Source readings | Targeted repair |
| --- | --- | ---: | --- |
| Control | Enabled | 2 | Enabled |
| Treatment | Disabled | 2 | Enabled |

Hypothesis: local repair recovers enough errors that generating a second complete
study adds little final quality. This comparison does **not** also remove the
second reading. Keep all other settings at the common control, even if study 1
looks favorable. That isolates refinement rather than measuring two changes at once.

Inspect `draft_extraction.json`, `refinement_extraction.json` when present,
`refinement_selection.json`, repair audits and final `extraction.json`. A refinement
candidate rejected by local checks is still a paid call. Score the final outputs;
use intermediate drafts only to explain changes, never to select the better answer
with reference access. Check whether refinement repairs family/specimen boundaries
or incorrectly changes supported concentrations and chemical associations.

Count changes in downstream repair calls and total spend: repair is allowed to
respond to the different drafts, so saved rewrite tokens are not necessarily the
net saving. This is the effect of removing the stage from the deployed workflow,
not a claim that the remaining downstream calls are identical.

## Study 3: is the claim ledger better than direct extraction?

Configuration: `examples/ablations/03-direct-extraction.json`.

| Arm | Evidence passed to study assembly | Global refinement |
| --- | --- | --- |
| Control | Grounded claim ledger plus cited passages | Enabled |
| Treatment | Complete parsed scientific evidence, no ledger calls | Enabled |

Hypothesis: a strong model can construct the study directly without losing important
facts, while avoiding the ledger's output cost and its potential omission bottleneck.
Keep refinement, enrichment, repair settings and budgets unchanged. This is **not**
a one-call pipeline: only `--claims` changes. A later combined test can remove both
claims and refinement if the individual studies support that combination.

The intervention also changes available audit information: without a claim ledger,
claim-coverage repair cannot identify the same missing claims. This is a total-system
comparison of using a ledger, not an isolated test of JSON formatting. Local checks
still run, but source occurrence does not establish scientific correctness.

Report results separately for short and long SI. The existing no-claims path uses
global source evidence; it does **not** implement direct-extraction windows. A paper
that exceeds the assembly input limit must remain a visible failure. Do not truncate
the SI or quietly omit that paper. Confirm context support with `--dry-run` before
paid runs; if comparing only the context-feasible subset as a secondary analysis,
define it before inspecting any model output and retain whole-roster failure counts.
Any future direct-window implementation would be a different experiment.

Inspect missed devices, erroneous family splits, champion/average conflation and
precursor-to-concentration associations. Lower call count does not guarantee lower
cost: direct evidence can make assembly/refinement inputs much larger.

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
