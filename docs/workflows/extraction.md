# Extract a study

PERLA reads a paper and its supplement, builds records for the reported study, and
checks those records against the parsed source. The result is `extraction.json`.
Database interpretation and export happen afterward and do not rewrite that result.

The default includes two source readings and a second review of the complete draft.
These are configurable attempts to improve recovery and attribution. More passes do
not establish better accuracy; compare configurations against reviewed references.

```text
source = parse(main_paper, supplement)

if source_reading_enabled:
    windows = fit_reading_requests(source, instructions, citation_schema)
    readings = read_each_window(windows, passes=2)
    notes = combine_exact_repeats(readings, retain_provenance=True)
    notes = check_quotes_subjects_and_raw_values(notes, source)
    evidence = select_supporting_passages(notes, source) if notes else source
else:
    notes, evidence = [], source

study = build_one_complete_study(evidence, notes)
if draft_review_enabled:
    candidate = review_complete_draft(study, evidence, notes)
    study = select_without_increasing_technical_issues(study, candidate)
if targeted_repair_enabled and a_resolvable_issue_exists(study):
    candidate = repair_requested_records(study, relevant_source)
    study = select_without_increasing_technical_issues(study, candidate)
study = remove_optional_values_that_fail_source_checks(study, source)
save(study, validation_findings, provenance)

interpretations = []
if interpretation_enabled:
    interpretations = interpret_composition_and_processing(study, source)
export(study, interpretations)
```

## What each stage means

| User task | Code terminology | Input and result | When it helps; limitation |
| --- | --- | --- | --- |
| Prepare source | parsing, evidence blocks, spans | PDF text and tables become located passages | Keeps exact source wording available; parser errors remain possible |
| Read source statements | claims, atomic facts, claim ledger | Source becomes notes about subjects and individual values or outcomes | Can reduce long input and expose omissions; an omitted note can hide a fact from later stages |
| Build the study | assembly | Notes plus original passages become one complete study | Resolves specimen identity across the paper and SI; remains the main global reasoning task |
| Review the draft | refinement, reconciliation | Same evidence plus the draft produce a complete replacement candidate | May correct identity or omissions; can also damage correct fields |
| Correct a visible issue | targeted repair | A worklist, affected records, and implicated passages produce bounded replacements | Limits edits to identified problems; cannot discover every unseen source fact |
| Check final source support | validation, conservative finalization | Source checks produce findings and, in narrow cases, remove optional unsupported values | Provides technical integrity, not proof of scientific accuracy |
| Interpret for a database | enrichment | Existing reported facts become composition and processing interpretations | Useful for export; needs a separate assessment from reported-fact extraction |

A **source statement** is one assertion with source evidence. An **atomic value** is
one semantic quantity, such as one concentration or one efficiency, including its
reported range or uncertainty. A **claim ledger** is the collection of these notes
and their subjects. It is a fallible intermediate result, not ground truth.

A **scored fact** is different: it is a unit of evaluation credit derived from a
field in the final study, with its scientific context. Scored facts need not map
one-to-one to reader notes. See the [scoring rules](evaluation.md#what-is-scored)
for the exact denominators.

A **device family** describes a photovoltaic design. An **individual device** is a
particular specimen. A **performance observation** describes a measurement, a
**population statistic** describes an aggregate over devices, and a **stability
test** describes an aging experiment. Equal values or a shared fabrication recipe
do not establish that two measurements concern the same specimen.

## Reading and source selection

Every reader sees original parser text, never an earlier reader's response. The
second reading changes its attention instructions. Readers share the model, source,
and scientific rules, so their errors may be correlated. Agreement is not independent
confirmation of the paper.

Repeated notes are coalesced only when subject descriptions, scope, values, targets,
and exact citations agree. Distinct subjects with equal concentrations remain
separate. Ambiguous repeated subject descriptions inside one reading are not merged.
`claim_readings.json` retains the original reading IDs behind each retained entry;
the number of supporting readings is not a confidence score.

Before notes guide assembly, Python checks that cited quotations occur in their
source blocks, referenced subjects exist, and a claimed raw value occurs inside its
cited quotation. Scientific case and numeric boundaries matter: `Co` is not `CO`,
and `20` is not `120`. This establishes textual support, not correct attribution.
`claim_grounding.json` records rejected entries and reasons.

The final reported-value checks also preserve case, charge, superscripts, and
subscripts. A value must occur inside its cited quotation; another sentence in the
same block cannot supply it. Reusing a unit explicitly printed in a table header is
a separate source-supported exception. Layout whitespace and an observed PDF
uncertainty-glyph artifact have narrow matching rules; they do not permit changing
chemical notation or a number's sign.

For usable notes, assembly receives their supporting passages, one neighboring span
on each side within the same block, and the main paper's opening page for metadata.
This neighborhood is a cost-control heuristic. A distant table header, footnote, or
cross-reference may still be needed to interpret a value. Facts omitted from both
the notes and selected text cannot reliably be recovered during draft review.

With `--no-claims`, or when no usable notes remain, assembly and draft review receive
**the complete parsed source, including SI**. Their citation schemas include the
same complete source. This is the direct-extraction baseline.

## Context limits and model choice

`--claim-mode auto` uses a complete-source reading when its serialized request fits
`--single-call-max-input-tokens`; otherwise it uses windows. Each window covers
complete parser blocks. Every source block is primary evidence once per reading.
Small main papers may also accompany SI windows as context for resolving terms.

The planner measures the complete request: instructions, system text, source
serialization, citation IDs, and strict response schema. It budgets for the longest
configured reading prompt. It removes optional repeated context before splitting a
window at block boundaries. An indivisible oversized block raises a planning error;
it is not silently truncated or sent beyond the planned limit.

| Setting | Default | Meaning |
| --- | ---: | --- |
| `--single-call-max-input-tokens` | 90,000 | Input estimate threshold for one complete-source reading |
| `--claim-window-input-tokens` | 60,000 | Input estimate cap for each windowed reading |
| `--assembly-max-input-tokens` | 180,000 | Shared input cap for building, reviewing, repairing, and interpreting records |
| `--claim-max-output-tokens` | 30,000 | Reader output allowance |
| `--max-output-tokens` | 80,000 | Study-building and draft-review output allowance |
| `--repair-max-output-tokens` | 30,000 | Targeted-repair output allowance |
| `--enrichment-max-output-tokens` | 20,000 | Interpretation output allowance |

These are engineering defaults, not scientifically validated thresholds. A deployment
can set smaller input and output limits and declare its total context capacity.
The client checks each actual request, including a validation-correction follow-up,
against those limits. When context capacity is declared, input plus reserved output
must fit. Smaller reader windows alone cannot solve an oversized global study request.

Without a local tokenizer, input sizes use an explicitly labeled UTF-8/3 heuristic
plus a framing allowance. They are not actual provider token counts or a guarantee
about the provider's internal formatting. Use a local tokenizer and explicit
capabilities for the selected deployment where available; inspect recorded estimates
and provider usage. Large advertised context capacity does not establish reliable
extraction at that length.

An unspecified stage model inherits the main model. Stage deployments can supply
explicit model and context settings; the applied choices are recorded in
`run_configuration.json`. See the [CLI reference](../reference/cli.md) for options.

## Choosing a configuration

Use the current default when reproducing the existing method. To establish whether
its extra stages help a new dataset, start with complete-source extraction and
checks (`--no-claims --no-refinement --no-targeted-repair --no-enrichment`). Then
compare one change at a time: conditional repair, one reading, a second reading,
and complete-draft review. Keep source files, models, output allowances, reference
annotations, and scoring rules fixed for the comparison under study.

Use a reader for documents that do not fit directly, or when experiments show a
useful gain. Use `--claim-recall-passes 1` to measure the value of the second reading.
Use `--no-refinement` to measure the value of the full rewrite. Targeted repair is a
separate intervention; it can be compared with or without global review. Record
failed and rejected attempts alongside successful ones, including their costs.

Interpretation serves a different output task. Disable it for a reported-fact study,
or retain it and evaluate its correctness separately when database export matters.
The fact score cannot establish the correctness of an interpretation that does not
change `extraction.json`.

## Checks, corrections, and their limits

`claim_coverage_audit.json` compares the study with the admitted source notes. It
lists matched, possible, and unmatched statements; unsupported records; and shared
quantities whose targets were not represented individually. A matching citation
alone does not establish that the correct value was extracted. Missing notes are
outside this audit's view, so it is a review queue, not a recall or precision score.

A targeted repair request includes only the record types and existing IDs needed by
its worklist. New records or removals require an explicit corresponding work item.
The model returns complete typed replacements, so dependent fields are validated
together. Python restores omitted collections as empty and filters changes against
the worklist. Unrequested records remain unchanged.

Global review and local repair use the same acceptance rule: neither validation
issues nor semantic claim-coverage issues may increase. Tied counts may accept a
changed candidate. Such a gate can still miss a wrong association between a valid
number and a specimen. Assess accepted corrections, newly introduced errors, and
rejected proposals against reviewed references. Rejected model work still costs money.

After corrections, finalization may remove an optional atomic value or material-form
label that fails its source-support check. Removal must leave the typed study valid,
reduce the total issue count, and increase no issue category. It does not remove
required records or guess ambiguous relationships. The original candidate and exact
removed paths are retained whenever this happens.

The workflow is text-only. It does not digitize plots or send rendered PDF pages to a
model. Unreadable formulas remain review items rather than guessed chemistry.

## Runs and failures

Use a **new output directory for every attempt**, including `--dry-run`. An existing
nonempty directory is rejected before parsing or writing. A dry run records its own
parse, configuration, and request plan; use another directory for the live run.
This prevents an old prediction from acquiring new source or model metadata.

Dry-run estimates cover the known source and request contracts. Reader notes and
future drafts do not exist yet, so later input sizes and conditional model calls
cannot be known exactly. Actual calls are checked again at the provider boundary.
For direct extraction, the complete assembly request is already known: a request
that exceeds its declared input or context limits produces a failed dry-run report
and a nonzero CLI exit, without contacting a model. Reader-mediated assembly is
explicitly marked conditional on the generated notes.

A failed first assembly produces a valid empty study and a failed report. A failed
optional draft review retains the valid initial draft; rejected repairs retain their
input. Partial reader failures remain explicit. If all notes are unusable, the
complete-source fallback is attempted within its input budget. Unexpected downstream
failures retain known model charges and a failure report rather than disappearing
from evaluation.

`events.jsonl` records stages, actual attempts, cache hits, source selection, and
candidate decisions under one run ID. Optional Langfuse tracing observes this same
execution; local events remain available if telemetry fails. `run_manifest.json`
binds configuration, report, source, and prediction bytes so evaluation can detect
changed or mismatched artifacts.

| Artifact | What to inspect |
| --- | --- |
| `extraction.json` | Final reported study |
| `document.json`, `evidence_spans.json` | Parsed source and exact citation passages |
| `run_configuration.json`, `claim_window_plan.json` | Applied settings, source identity, and reader plan |
| `claim_ledger.json`, `claim_readings.json`, `claim_grounding.json` | Notes, repeated-reading provenance, and rejection reasons |
| `validation.json`, `claim_coverage_audit.json` | Technical source and coverage findings |
| `draft_extraction.json`, `refinement_audit.json`, `refinement_selection.json` | Draft-review changes and candidate decision |
| `targeted_repair.json` | Repair worklist and acceptance decision |
| `conservative_finalization.json`, `pre_conservative_extraction.json` | Removed optional values and the preceding candidate |
| `requests/`, `events.jsonl`, `report.json` | Exact requests, attempts, outcomes, usage, and cost |
| `run_manifest.json` | Integrity binding used when scoring a run directory |
| `enrichment.json`, `nomad/` | Separate interpretations and database export |

Files depend on enabled stages and their outcomes. For interpretation and export,
see [Interpret composition and processing](enrichment.md) and
[Export to NOMAD](nomad-export.md).

Interpretation has at most three semantic calls: composition, an optional composition
retry for unresolved components, and processing. Provider or schema-validation
retries are additional attempts and are recorded separately.
