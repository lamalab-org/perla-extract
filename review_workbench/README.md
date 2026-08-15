# Study ground-truth workbench

This app turns a rich model extraction into reviewed ground truth without losing the
model seed or the human decision trail. It reviews the complete `StudyExtraction`,
including composition, layers, processing, performance, population statistics,
stability, and explicit identity links.

## Review sequence

1. Import `extraction.json`, `document.json`, the main PDF, and optionally the SI and
   `run_configuration.json`.
2. Search the main paper and SI and submit a device census before candidates appear.
3. Mark every candidate as verified, uncertain, or needing correction. Add, replace,
   duplicate, or remove complete records. Additions and replacements require an
   exact quote from an imported evidence block. The complete result is validated after
   every change.
4. Complete the inventory and field-review stages.
5. Check the final completeness gates and record remaining ambiguity.

The blind census matters: a reviewer who starts from model candidates is likely to
correct their values but overlook devices the model never proposed.

## Stored artifacts

For each paper the workbench keeps:

- `seeds/<split>/<paper>.json`: immutable model output;
- `<split>/<paper>.json`: compiled, validated rich ground truth;
- `events/<split>/<paper>.json`: reviewer, timestamp, prior value, new value,
  evidence, record decisions, and stage decisions;
- `documents/<split>/<paper>.json`: supplied evidence blocks;
- `manifests/<split>/<paper>.json`: schema, model configuration, and seed digest.

The rich ground truth is authoritative. Produce the reduced PERLA representation with
the deterministic adapter; do not curate the lossy representation independently.

## Run locally

From the repository root:

```bash
python review_workbench/server.py \
  --pdf-dir review_pdfs \
  --ground-truth-dir review_data
```

Then open <http://127.0.0.1:8765>. The local server uses a local administrator identity.

## Deploy

The deployment adapter stores PDFs and mutable JSON in private Vercel Blob storage.
The prepared bundle contains no papers or ground truth.

```bash
python review_workbench/prepare.py
vercel link --cwd review_workbench/.vercel-build --yes --project perla-ground-truth-review
vercel deploy --cwd review_workbench/.vercel-build --prebuilt
```

Authentication can use fixed internal accounts (`REVIEW_INTERNAL_ACCOUNTS` and
`REVIEW_SESSION_SECRET`) or Clerk (`NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`,
`CLERK_SECRET_KEY`, and reviewer/admin allowlists). Secrets never reach the browser.

## Verify

```bash
python -m pytest -q review_workbench/tests
```
