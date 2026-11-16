# Context scoring validation: 16 November 2026

The evaluator now has an explicit measurement-condition field, a shared dependency
table, and automatic diagnostics for every fact. No new human labels, model calls,
or reference corrections were used. The extraction outputs themselves did not
change. See the [method](../methods/scientific-scoring.md) and
[developer entry point](scoring.md).

## What changed

- `measurement_conditions` is available on individual observations and population
  statistics. Registered conditions in archived `metrics` arrays use the same
  canonicalization. Preparation and aging fields are not relocated.
- Canonical process names and their aliases use identical requirements. Once the
  ordered operation and target are established, missing optional ingredients do
  not invalidate annealing temperature/duration or spin-coating speed/duration.
- PCE/Voc/Jsc/FF stay in their declared reporting denominator even when their value
  cannot be parsed. Literal interpretation is reported separately. Population
  claims remain in this bin with their statistical level retained in diagnostics.
- Unknown conditions remain literal context and cannot disambiguate identity.
  Missing or conflicting defining context still prevents attributed credit.
- Reports and experiment CSVs expose the applied rule, context availability,
  parsing status, original paths and overlapping candidate blockers. No extra
  experiment configuration or review step is required.

## Saved-extraction replay

All 206 original roster entries were retained. The 117 scoreable outputs comprise
82 completed and 35 partial outputs, from 21 papers in the 30-paper development
study. Failed/infeasible attempts remain unscored, not zero-valued successes.

| Pooled diagnostic | Before | After |
| --- | ---: | ---: |
| Completed outputs: attributed matches | 1,168 | 1,172 |
| Completed outputs: attributed F1 | 6.8805% | 6.9041% |
| Completed outputs: recovery F1 | 20.4177% | 20.4177% |
| All scoreable outputs: attributed matches | 1,529 | 1,533 |
| All scoreable outputs: attributed F1 | 5.7504% | 5.7654% |
| All scoreable outputs: recovery F1 | 18.2290% | 18.2290% |

These are pooled development diagnostics, not a ranking of models or an estimate
of extraction accuracy. Completed-output denominators are 25,236 reference facts
and 8,715 predicted facts. All-output denominators are 40,836 and 12,343. The
reference is repeated for different attempts; these are not independent papers.

The four newly attributed facts are 3,000 rpm spin-coating speeds in two saved
`10.1038/nmat4014` outputs (`read1/r1` and `repair/r2`, two families each). Before,
`spin-coating speed` received local requirements but the already canonical name
`speed` still required the optional ingredient list. Both now use the same rule.
Wrong operation/target and wrong-value controls still reject attribution.

No attributed credits were lost. Every canonical fact, source alias, record
assignment and recovery assignment is unchanged. Reporting-bin changes move
existing malformed/qualitative photovoltaic claims into the fixed concept scope;
they neither remove facts nor award new matches. Serial uncached and parallel
cached replays produced identical complete report JSON. All 2,186 files in the
three earlier sealed studies verified byte-for-byte unchanged.

## Automatic validation

The frozen-reference audit passed 139 existing perturbation controls and 435 new
context controls: 15 transformations applied to 29 references. One reference had
no suitable individual measurement; its 15 cases are explicitly inapplicable.
All 30 reference self-checks passed. The generated controls are synthetic local
experiments with recorded lineage and stated expected credit, not new annotations
of paper evidence. They are now part of CI.

The test suite additionally covers ordered processing alignment, independent
attribute omissions, wrong owners, duplicate owners, population/statistic
mismatches, aging/preparation separation, ranges, uncertainty and exhaustive small
assignment oracles. Source IDs and shared paragraphs cannot supply an unvalidated
anchor. Original reference files contain document hashes but not the precise
shared source locations needed by an anchor resolver, so anchor coverage remains
unavailable; missing measurement protocol is not relaxed.

Among the 8,715 predicted facts from completed outputs, 1,395 have at least one
unambiguous candidate satisfying all attribution context, independently of value
agreement. This is a context-availability diagnostic, not calibrated confidence.
Missing identity and defining context remain substantial limitations. Population
and stability attribution are not declared solved by these controls.

## Runtime and reproducibility

The audit executes one paper per worker with bounded concurrency and retains one
verified reference within each paper task. Its manifest binds the input, schema,
policy source hashes, script and environment. Production normalization caches
only immutable text and quantity tuples, not owner mappings. Cached/uncached
parity is tested, and candidate work exceeding one million pairs for a concept
fails explicitly without a truncated score.

Synthetic measurements separated paper throughput from candidate density. At
10, 50 and 100 measurements per paper, median dense-case scoring times over three
repeats were 0.0083, 0.0921 and 0.3508 seconds before, and 0.0071, 0.0521 and
0.2107 seconds after. Process peak memory for that small workload was about
77.4 MB before and 73.6 MB after. These are local development measurements under
shared machine load, not a production capacity claim or evidence of linear dense
assignment scaling. Full replay timings and complete decision ledgers are retained
in the local audit artifacts.

The ordinary interface remains `perla-experiment experiment.toml --check`, then
`perla-experiment experiment.toml`. Read `summary.md`, both score views and
`figure-data/scoring_diagnostics.csv`. Automatic paired comparisons remain
conditional on complete matching runs and equal-paper weighting; unresolved facts
stay in their denominators. No per-output human labeling is required.
