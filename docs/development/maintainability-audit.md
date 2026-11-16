# Maintainer audit: 16 November 2026

This pass inventoried all 110 Python modules in the package, review workbench and
examples, traced the experiment/extraction/scoring/export entry points, and reviewed
the reviewer and paper-discovery boundaries. It also exercised the documented offline
onboarding path and the complete test suite. This is an engineering review, not an
observed usability study with new students or a claim that every branch is defect-free.

## Changes made

| Problem | Change | Verification |
| --- | --- | --- |
| One large scorer function mixed five scientific record types | Separate family, specimen, performance, population and stability projections; retain shared context and quantity handling | All 117 saved evaluation reports and 147 complete independent fact projections remained identical |
| Exported values could lose preparation and measurement context | Preserve measurement conditions, specimen/cohort treatments and statistical metadata in both export formats' notes | One regression scenario checks individual, population, standalone-specimen and aging records in both adapters |
| A valid automatic comparison appeared beneath “Comparison unavailable” | Lead with the available reference-agreement result; explain the separate reviewed-scope limitation | Complete, partial and entirely failed experiment cases |
| The first experiment required understanding a 30-paper roster | Start with the existing one-paper TOML; keep the larger roster optional | A real paper/SI/reference mapping passed `perla-experiment --check` |
| New maintainers had no map covering the whole repository | Add a responsibility map and an explicit scorer reading path | Paths checked against the checkout; strict documentation build |
| Some descriptions were stale or overstated scientific guarantees | Distinguish corrected records from source-wide coverage; document treatment/measurement/aging placement; correct the historical composition example | No matching rules, reference labels, numeric tolerances or extraction prompts changed |
| Nullable context and empty condition lists required raw JSON editing | Add schema-driven object and list controls with explicit citation guidance | Browser add/clear and list add/remove checks; typed draft and save-boundary tests |
| Review cards hid fields used by scientific scoring | Display specimen/cohort treatments, measurement conditions and error-bar metadata; retain owning source paths | Imported renderer tests, deployment bundle check and browser verification |
| Malformed unit text aborted scoring with the Pint release used on Python 3.10/3.11 | Reject unsupported unit syntax consistently and treat parser assertions as unparseable values | Reproduced the CI failure; all 117 full reports match on Python 3.11/Pint 0.25.3 and Python 3.12/Pint 0.26.2 |
| A truncated response appeared only as “invalid JSON” | Include the provider's truncation status, configured output allowance and retained response path in the error | Exercise the actual response decoder; retain usage and avoid another request with the same limit |

The fact-projection refactor uses a private collector for one input at a time.
Its methods select fields; they do not identify matching records or inspect the
other input's values. The public function retains collection order and merges the
same explicitly redundant representations. No new registry, configuration format
or alternative scorer was introduced.

## Checks exercised

- Full repository test suite: **1,234 passed, 63 skipped** in its default invocation.
  The separately enabled reference-release suite passed **124 tests**; optional external checks
  are not evidence of live Docling-model or pinned NOMAD-normalizer compatibility.
- Automatic controlled contrasts: **574 passed**, with no new labels or model calls.
- Browser journey: saved and reloaded a specimen-owner correction, changed PCE from
  21% to 20%, inspected both reversible edits, and exercised the new context controls
  using the synthetic demo only.
- Ruff and type checking: passed; mypy checked all 66 extraction modules.
- Strict documentation build: passed.
- Offline scoring example: all four independently specified cases passed.
- Request-planning example: fitting input reached `dry_run`; the impossible limit
  failed before any model request.
- Saved-data parity: 117 full evaluation JSON reports matched the preceding v17
  replay exactly; 147 full fact projections contained the same 25,673 facts, scopes,
  linked containers and conflict records. The projection check covers 30 references
  and 117 saved predictions. It does not treat repeated reference facts as new labels.
- Cross-version replay after the unit-parser fix: all 117 reports match the same
  baseline on both Python 3.11/Pint 0.25.3 and Python 3.12/Pint 0.26.2.

The accompanying live open-model study uses a separate frozen checkout. Editing
this checkout therefore cannot change a model prompt halfway through that study.
All old extraction and evaluation artifacts remain immutable.

## Remaining work

The research entry points are narrower, but several optional subsystems remain
large. `review_workbench/study_review.py`, `server.py` and `spreadsheet_review.py`
combine substantial review, HTTP and workbook behavior. `nomad.py` and
`compatibility.py` still contain long explicit field projections. A future change in
one of these areas should extract a cohesive operation with a behavior test, rather
than introduce a generic adapter framework or split files by line count alone.

The largest remaining family projection combines layer, absorber and recipe facts
because they share explicit layer links. Its field-selection behavior is readable in
one place but still deserves care when extending tandem or process support.

A full package installation still includes Docling and its model dependencies,
even when an experiment selects PyMuPDF. The offline scoring example has a small,
pinned environment. Making Docling optional is a worthwhile packaging follow-up,
but it must be coordinated with the current Docling parser default and documented
installation behavior; this pass does not silently switch the scientific parser.

Provider compatibility remains empirical. A model listed by a service may answer a
short chat yet time out or violate the full extraction schema. Keep a synthetic
full-schema check and real-paper completion accounting separate from scientific
scores. Missing responses and unavailable deployments must not become zero quality
scores or silently disappear from a study roster.

A useful next human check is to ask a new student to run the offline example, explain
one wrong-scan score, prepare one paper/SI/reference block, and locate the code that
selects a performance field. Record where they need help. Passing automated tests
alone cannot establish that this journey is easy for them.
