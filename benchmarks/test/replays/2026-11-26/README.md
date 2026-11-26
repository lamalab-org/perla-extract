# Saved-extraction replay, 26 November 2026

This compares scoring changes on **unchanged extractions**, without model calls.
It does not measure extraction improvements from the evidence/context fixes.

## Inputs and accounting

410 distinct, sealed attempts cover all 30 available human-reference papers.
221 attempts completed; 154 failed and 35 were partial. Every attempt remains in
the replay. Selection requires a recorded live extraction call and matching
article/SI hashes; synthetic/check artifacts and unbound sources are excluded.
Duplicate copies of the same run ID count once.

Baseline production commit: `8a2d73c704da6cc1190ae91da82e0813de86f901`.
The quantity-module move alone produced **410 byte-identical evaluation reports**.
Both scorers use Python 3.12.14, Pydantic 2.14.3 and Pint 0.26.2, with unchanged
1% relative / zero absolute tolerance. Extraction prompts are unchanged.

The revised implementation is frozen at `76ab469`. Delta review caught an
outcome-filter regression that removed the code from `Device 1 PCE distribution`;
the corrected comparison preserves the designation and tests that actual case.

## What changed

The final comparison adds **39 attributed matches and 172 paper-wide recovery
matches**, with no lost credit or inventory changes. Gains preserve approximations,
handle split micro-prefixes and recognize explicit humidity suffixes. Source-path
change counts also include aliases; they are not counts of additional facts.

The table pools the **221 completed attempts over 24 papers**, with before → after
matched counts. Failed and partial attempts remain in the separate all-attempts
scope; the six papers without a completed attempt are not hidden there.

| Field | Attributed matches | Recovery matches | Predicted | Reference |
| --- | ---: | ---: | ---: | ---: |
| PCE | 408 → 411 | 986 → 1,001 | 1,285 | 1,550 |
| FF | 302 → 302 | 670 → 670 | 782 | 1,363 |
| Voc | 306 → 306 | 712 → 712 | 780 | 1,390 |
| Jsc | 298 → 298 | 617 → 617 | 725 | 1,352 |
| Relative humidity, aging protocol | 0 → 0 | 8 → 32 | 53 | 57 |

For example, PCE recovery precision changes from 76.7% to 77.9%, and recall from
63.6% to 64.6%. Attribution remains much lower: these representation fixes do not
resolve missing or ambiguous device/context information. The CSV records both
views' precision, recall and F1 without changing their denominators.

## Read the outputs

- `field-summary.csv`: before/after counts, precision, recall and F1 for attribution
  and paper-wide recovery. All attempts, completed attempts and the recent pilot
  are separate scopes.
- `summary.json`: coverage, policy changes and scientific limitations.
- `input-manifest.json`: original run IDs, statuses and input hashes. Full saved
  predictions, paper text and local filesystem paths are not published here.

Completed-attempt totals pool mixed historical settings and repeated papers.
They are diagnostic, not a model ranking or an independent-paper accuracy estimate.
The four-paper pilot is too small for a strong statistical conclusion.

## Correctness checks

The ordinary suite passes **1,677 tests**. All **124** reference-release tests,
**three** native/OCR Docling tests and **three** installed NOMAD/prepared-deployment
integration tests pass. The 65 skips in the default environment are accounted for
by those dedicated checks rather than treated as integration evidence themselves.
Automatic controls pass all 463 applicable context perturbations, 139 applicable
inventory/assignment controls and 17 independently specified equivalence probes.
Required scientific cases have explicit expected meanings; unsupported parsing
cannot silently remove them from test coverage. Ruff, mypy and strict docs pass.

Four active-area annotations in `10.1002/solr.201900370` store `0.06 cm -2`.
That inverse-area unit conflicts with an area quantity. The files and denominators
remain unchanged; these claims are flagged and receive no numerical credit, even
against themselves. This needs reference-source review before a clean area-accuracy
claim. There are no paper-specific scoring exceptions.

The human references already informed development and have incomplete coverage
review. This replay is not an untouched held-out evaluation. Passing software
checks does not establish that a model extracted every supported fact.

The replay flags 167 inconsistent prediction occurrences, including OCR confusions
between `uC`/`°C`, `mm`/`mM`, and `ml`/`µL`, missing decimals and split unit fragments.
Some parsed values are supported by other cited evidence despite damaged raw
strings. These are inconsistencies in the saved representation, not proof that
the physical values in the papers are wrong. The scorer does not guess a repair
from the paired reference. Fixing source/claim completeness needs new extraction
comparisons under a frozen scorer after PR review.

To repeat the comparison, use the existing
[saved-replay command](../../../../docs/development/scoring.md#replay-saved-extractions)
on the baseline and revised checkouts with the same dependencies. Preserve every
input row and choose fresh output directories. Review per-path gained/lost credits
and assignments before interpreting any F1 change.
