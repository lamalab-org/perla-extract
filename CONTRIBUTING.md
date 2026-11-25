# Contributing

Start with the [maintainer exercises](docs/development/maintaining.md): trace a
request limit without model calls, explain a score, and correct an invented paper
in the reviewer UI. These exercises show where the main responsibilities live.

## Code Contribution

This project uses the [GitHub Flow](https://guides.github.com/introduction/flow)
model for code contributions. Follow these steps:

1. [Create a fork](https://help.github.com/articles/fork-a-repo) of the upstream
   repository at [`lamalab-org/perla-extract`](https://github.com/lamalab-org/perla-extract)
   on your GitHub account (or in one of your organizations)
2. [Clone your fork](https://docs.github.com/en/repositories/creating-and-managing-repositories/cloning-a-repository)
   with `git clone https://github.com/<your namespace here>/perla-extract.git`
3. Make and commit changes to your fork with `git commit`
4. Push changes to your fork with `git push`
5. Repeat steps 3 and 4 as needed
6. Submit a pull request back to the upstream repository

### Merge Model

This repository uses [squash merges](https://docs.github.com/en/github/collaborating-with-pull-requests/incorporating-changes-from-a-pull-request/about-pull-request-merges#squash-and-merge-your-pull-request-commits)
to group all related commits in a given pull request into a single commit upon
acceptance and merge into the main branch. This has several benefits:

1. Keeps the commit history on the main branch focused on high-level narrative
2. Enables people to make lots of small commits without worrying about muddying
   up the commit history
3. Commits correspond 1-to-1 with pull requests


### Code formatting, etc.

Install the development environment and run the local checks with:

```bash
pip install -e '.[dev]'
node --version  # Node.js 20+ is required for behavioral reviewer tests
ruff check src tests review_workbench examples
python -m mypy src/perla_extract/study_extraction
python -m pytest -q -ra
```

### Choose the check that answers your question

| Change | Required check | What it establishes |
| --- | --- | --- |
| Quantity meaning, scoring or export | `python -m pytest -q -ra tests/study_extraction/test_quantities.py tests/study_extraction/test_scoring_example.py tests/study_extraction/test_nomad_export.py` | Independently expected conversions, credit and export behavior, including preserved contradictions |
| Evidence selection or correction | `python -m pytest -q -ra tests/study_extraction/test_source.py tests/study_extraction/test_claims.py tests/study_extraction/test_spans.py tests/study_extraction/test_identity_retention.py` | Connected evidence transport and retained scientific distinctions |
| Reference loading or scoring policy | `PERLA_RUN_REFERENCE_RELEASE_TESTS=1 python -m pytest -q -ra tests/study_extraction/test_reference_release.py` | Frozen release integrity and explicit reference controls |

The scientific tests block network connections. Missing mocks fail before a
provider can be contacted, and parser weights must be prepared separately. The
worked example below needs neither provider SDKs nor PDF models. Passing these
checks establishes software behavior on declared cases, not extraction accuracy.

For parser changes, also run the real Docling content and OCR integrations after
installing dependencies and preparing their local model weights:

```bash
pip install -e '.[dev]'
PERLA_RUN_DOCLING_TESTS=1 PERLA_RUN_DOCLING_OCR_TESTS=1 python -m pytest -q -ra tests/study_extraction/test_docling_integration.py
```

The native and rasterized authored fixtures check known table cells, blank and
merged cells, caption/note links and citation decoding. A smoke test with OCR
disabled does not establish OCR quality. A required integration that is skipped
remains untested and blocks acceptance of a change relying on it.

For export compatibility, run the pinned NOMAD contract, which also has a regular
CI job:

```bash
pip install -e '.[dev,nomad]'
PERLA_RUN_NOMAD_TESTS=1 python -m pytest -q -ra tests/study_extraction/test_nomad_integration.py
```

Build the documentation with strict link and reference checks:

```bash
pip install -e '.[docs]'
mkdocs build --strict
```

Use `mkdocs serve` while editing the site locally.

The public scoring example is exercised by the normal test suite. To inspect its
saved corrections and reports, choose a new output directory and run:

```bash
PYTHONPATH=.:src python -m examples.scoring.run --output /tmp/perla-scoring-example
```

Do not regenerate its expected counts from scorer output. Derive them from the
documented scientific cases and review any changes to the scoring contract.
For a failed case, compare `actual`/`expected` and `checks`/`expected_checks` in
`summary.json`, then follow its prediction's evaluation and NOMAD export.
Check rendered diagrams, equations and tables as well as the strict build.

Docstrings are expected at public APIs, scientific model boundaries, persistence
transitions, and non-obvious algorithms. Explain invariants, side effects, or why a
choice exists; do not add a docstring to obvious private plumbing merely to restate its
name. Field descriptions remain the preferred place for leaf-schema constraints.

We also provide pre-commit hooks that format the code according to standard
conventions.

To set up the hooks:

1. Install pre-commit: `pip install pre-commit`
2. Install the hooks: `pre-commit install`

### Dependency Management

We use `pyproject.toml` for dependency management. Please ensure that any new dependencies are added to the appropriate section in `pyproject.toml`.

### Extraction implementation map

Keep user interfaces thin. `configuration.py` owns defaults, TOML validation,
stage inheritance and effective method identity; CLI and cohort execution resolve
through it. A repair limit override must not lose an inherited local endpoint.
`workflow.py` orders operations through named results in `workflow_results.py`;
`candidate_selection.py` owns the non-regression rule. `extraction_prompts.py`
owns scientific instructions, `source_reading.py` the reader, `study_outputs.py`
interpretation/export, and `run_reporting.py` report assembly. `source.py`, `claims.py`,
`refinement.py` and `repair.py` own their evidence transformations. The workflow
[guide](docs/workflows/extraction.md) explains the same stages without requiring a
reader to know these module names.

`quantities.py` interprets complete raw claims for validation, scoring and export;
`quantity_specs.py` contains their checked definitions, and `units.py` performs
numeric unit conversion. Preserve raw text and evidence, keep unsupported claims
visible, and keep statistical/ownership context outside quantity conversion.

`client.py` coordinates each logical model call; `accounting.py` owns typed call and
attempt records. Budget checks and report totals derive from those records. Account for paid
work before optional cache writes; a failed output write must not make a paid attempt
look free. `request_policy.py` carries one resolved stage policy into planning and dispatch;
`deployment.py` estimates the exact serialized request. `observability.py` writes canonical local events; `tracing.py` exports
those events to optional Langfuse observations. It must not introduce another model
call or duplicate automatic callbacks.

`reference_io.py` accepts one ground-truth artifact format (4). Structural schema
compatibility and descriptive prose fingerprints are distinct. Frozen reference
files remain immutable. `run_artifacts.py` binds extraction configuration, evidence,
prediction and accounting. Revalidation writes a new numbered directory; it never
rewrites a sealed report. Resume is conservative about source, code and dependency
changes. These integrity checks detect accidental mixing, not malicious authorship.

`comparison.py` reads an ordinary paired CSV roster, verifies saved attempts, and
resamples papers. `figure_data.py` exports its counts and diagnostics without
rescoring. Known cost/token subtotals must remain distinct from complete totals.
Do not turn cache hits into independent repetitions, count copied run IDs twice,
resample individual facts as papers, or omit failed attempts from a claimed cohort.

Test scientific behavior with hand-checkable positives and negatives: equivalent
units should match; missing units, charge/subscript differences, wrong devices and
unsupported source values should not. Test failure accounting and replay boundaries
with injected provider/filesystem failures. Frontend tests execute imported review
state helpers; verify changed user journeys against the real local backend as well.
Avoid tests that merely repeat generated plans, source-code shape or incidental copy.

The [reviewer UI maintenance guide](docs/development/reviewer-ui.md) includes a
one-command synthetic workspace. API methods, editor draft state and saved review
session state have separate owners. The [scoring guide](docs/development/scoring.md)
shows how to explain a specific missed fact using the evaluator's actual decisions.
