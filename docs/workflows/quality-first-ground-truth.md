# Extract a batch for review

`perla-extract-cohort` applies one configuration to a list of papers and records each
run's outcome. Use it to generate review drafts with consistent settings. A draft
becomes a reference only after source review and adjudication.

## Define the batch

Create `cohort.json`:

```json
{
  "format_version": 1,
  "name": "solar-cell-review",
  "purpose": "Generate drafts for source-based expert review",
  "split": "dev",
  "model": "openai/gpt-5.2",
  "parser": "docling",
  "claim_recall_passes": 2,
  "max_model_calls_per_paper": 14,
  "max_cost_usd_per_paper": 2.0,
  "papers": [{"paper_id": "10.0000--example"}],
  "exclusions": []
}
```

Replace the example paper ID and model with your inputs. Main PDFs must be named
`<paper_id>.pdf`. Supplements are resolved as `<paper_id>-SI.pdf` or
`<paper_id>.supplement.pdf`, in that order. If neither exists, the paper is processed
without a supplement. Inspect the source list before review.

The split is `calibration`, `dev` or `test`; it labels the batch and does not establish
that papers are independent of development. `exclusions` can retain considered
papers as objects containing `paper_id` and `reason`. Duplicate IDs and overlap
between included and excluded papers are rejected.

The repository's `data/study_extraction/cohorts/review-v1.json` is an existing
development cohort, not a required input list or a held-out benchmark.

## Run and resume

```bash
perla-extract-cohort \
  --manifest cohort.json \
  --pdf-dir /path/to/main-papers \
  --supplement-dir /path/to/supporting-information \
  --output-dir results/review-batch \
  --env-file /path/to/provider.env
```

Use `--limit 1` to check a first paper. The default extraction includes repeated
claim reading, reconciliation, targeted repair and enrichment; see
[Extract a study](extraction.md) for their behavior.

The monetary limit is checked between calls; one response can cross it. A configured
limit stops further requests if the provider does not return usable cost information.
See [request budgets](../reference/cli.md#model-request).

The batch writes `cohort_run.json` after each paper. Completed runs are reused when
their recorded model, parser, reasoning, budgets, claim-reading count, schema and
prompt fingerprints match the requested configuration. `--rerun` regenerates matching
runs. Keep new extraction runs separate from saved human-review state.

To divide a batch among workers, give each the same `--shard-count` and a distinct
zero-based `--shard-index`. Each worker writes a separate batch report.

## Check results before import

Inspect per-paper `report.json` and the batch report for failures. Reapply the current
evidence checks without model calls if needed:

```bash
perla-extract-revalidate --runs-dir results/review-batch
```

Revalidation updates `validation.json`, `grounded_values.json` and validation-related
fields in `report.json`, not extracted records, requests or cost history.
Import needs a schema-valid extraction and its matching evidence
document; unresolved evidence issues must be addressed first.

Keep the main paper, supplement, `extraction.json`, `document.json`,
`run_configuration.json` and `report.json` together. Retain optional claim, repair,
refinement and enrichment audits as context for reviewers. A status of `accepted`
in an enrichment audit means it passed automated checks, not human verification.

## Import into the workbench

From the repository root:

```bash
python review_workbench/import_runs.py \
  --runs-dir results/review-batch \
  --pdf-dir /path/to/review-pdfs \
  --review-data review_data/current \
  --split dev
```

The importer first uses the PDF paths recorded in `run_configuration.json`. If the
batch moved to another machine, place the main PDFs and supplements in `--pdf-dir`
using their original filenames, or `<paper_id>.pdf` and `<paper_id>.supplement.pdf`.

The importer rejects incomplete or evidence-invalid runs and does not replace an
existing immutable seed. The [deployment guide](../deployment/review-workbench.md)
documents hosted imports and explicit refreshes. An audited refresh appends a revision
and preserves earlier evidence, seeds and human history; it is not an in-place overwrite.

Reviewers check records against the source, correct errors and add omissions.
[Build ground truth](ground-truth-review.md) describes those actions;
[From corrections to a benchmark](review-to-benchmark.md) covers adjudication and export.
