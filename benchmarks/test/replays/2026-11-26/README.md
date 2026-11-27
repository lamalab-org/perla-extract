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
- `provenance.csv`: all 410 original run IDs, statuses and input, source and
  evaluation hashes. Multiple PDF hashes are separated by semicolons. This is
  provenance, not an experiment input. Full predictions, paper text and local
  filesystem paths remain outside the repository.

Completed-attempt totals pool mixed historical settings and repeated papers.
They are diagnostic, not a model ranking or an independent-paper accuracy estimate.
The four-paper pilot is too small for a strong statistical conclusion.

## Simplification check, 27 November

Both the file simplification (`d829665`) and the subsequent current-only cleanup
reproduce **all 410 evaluation reports byte-for-byte**, with identical inputs,
settings and dependencies and no changed decisions. The latter removes ten files,
old exports, format migrations and duplicate experiment interfaces; frozen
reference and extraction bytes remain intact.

The current suite passes 1,677 tests; all 101 active-reference/release checks,
76 final output/frontend checks and three installed export/deployment checks pass.
All three real Docling native/OCR tests pass in clean CI. Ruff, mypy, strict docs
and the offline scoring example pass. Older format support is removed without
changing the scientific method or its automatic controls.

## Original verification, 26 November

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

<details>
<summary>Reproduction metadata</summary>

```json
{
  "code_sha256": {
    "after": "3a73b3ae8a31bd9e3e88d61958549c217071dcbaa9c568f175a8613e075ea9ea",
    "before": "fde6fcc45d6854ec349f86001387033d5c787f468380c2c2f0d70084d359a61b",
    "refactor": "ea8c3b7cb9e5968dfa4abd55dd9db18bfd415035acf3f05cfb2c7956d57a85b5"
  },
  "config": {
    "descriptor_equivalences": [],
    "minimum_record_similarity": 0.35,
    "numeric_absolute_tolerance": 0.0,
    "numeric_relative_tolerance": 0.01,
    "operation_aliases": {
      "spin-coating": "spin coating",
      "spin_coating": "spin coating",
      "spincoating": "spin coating",
      "thermal annealing": "annealing"
    }
  },
  "current_only_cleanup": {
    "code_sha256": "ad03289f16083eb1ce3dfdd423e139d0d1567efb9ee7ae91419fa1902ff2fb69",
    "decision_changes": 0,
    "identical_reports": 410,
    "replay_manifest_sha256": "ac195291ba53ae4e34e233bd136ebef3d2013886e9d03cf046ec3b72184b6c77",
    "same_environment": true,
    "same_inputs": true
  },
  "environment": {
    "pint": "0.26.2",
    "pydantic": "2.14.3",
    "python": "3.12.14"
  },
  "input_manifest_format_version": 1,
  "refactor_parity": {
    "decision_changes": 0,
    "identical_reports": 410,
    "same_environment": true,
    "same_inputs": true
  },
  "replay_manifest_sha256": {
    "after": "3cf19b706889ed7c7cfa934efbfb50b52c829528da1295bd640ac86b75de6193",
    "before": "2a11a783598f9e88aa706f0f9667bcbac903e7f1c925d3fa42cacc16643e846b",
    "refactor": "331d68e63e5072b520954d9f9819f998581a63449a25bc2b04647ffd32df8e03"
  },
  "report_versions": {
    "after": {
      "fact_profile": "core-scientific-facts-v24",
      "format_version": 17,
      "matcher_version": "rich-study-hungarian-v27",
      "study_schema_sha256": "8c907b376309579bbb73d5137453107abdd508a12185c936bba9cbc678534364"
    },
    "before": {
      "fact_profile": "core-scientific-facts-v23",
      "format_version": 17,
      "matcher_version": "rich-study-hungarian-v26",
      "study_schema_sha256": "8c907b376309579bbb73d5137453107abdd508a12185c936bba9cbc678534364"
    }
  },
  "roster_sha256": "ad510f112dcd6ed95b1f556d2be6dca23df6aae139a9dfe9c6c0da07778f6c6c",
  "scoring_commits": {
    "after": "76ab46964df192af54b3aa290798bfb11586009b",
    "before": "8a2d73c704da6cc1190ae91da82e0813de86f901"
  }
}
```

The former JSON provenance is retained in Git history. Every attempt field and
hash round-trips exactly through the CSV. Full reports and decision ledgers remain
in the local frozen replay artifacts.

</details>
