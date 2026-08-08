# Ground-truth review and scoring workflow

## Repository layout

Keep source material, benchmark truth, review state, and model output separate:

```text
PDF / paper record
  -> ground_truth/{dev,test}/{paper_id}.json
  -> evidence/{dev,test}/{paper_id}.json
  -> collaboration/{comments,issues}/{dev,test}/{paper_id}.json
  -> extractions/{model}/{paper_id}.json
  -> evaluation run artifact
```

- `dev` is available for prompt, schema, normalization, matching, and tolerance
  development.
- `test` is held out for final comparisons. Ground-truth corrections are
  allowed, but they should be review-driven and recorded before rerunning a
  reported benchmark.
- `review_metadata.json` owns paper-level eligibility. Article type and tandem
  scope do not belong in the cell extraction schema.
- Evidence, comments, and issues are review records, not benchmark facts. A
  reported issue only changes the ground truth after a reviewer resolves it.
- Model output is immutable input to a scoring run. Store the model identifier,
  code revision, ground-truth revision, eligibility rules, and tolerances beside
  every published result.

## Review gates

1. Classify the paper and exclude reviews, perspectives, and News & Views
   articles. Tandem papers remain eligible, but complete tandem-device records
   do not belong in this single-junction ground truth.
2. Review each included cell's identity first: composition, architecture, layer
   stack, and whether separate device variants have separate cell records.
3. Verify non-null scalar fields with a page and a short evidence quote.
4. Turn plausible omissions or corrections into evidence packets: exact quote
   and page, source type, device identity, measurement linkage, counterevidence,
   scope check, and a guarded JSON Patch when the change is unambiguous.
5. Accept, reject, or defer atomic proposal groups. Schema validation and the
   durable decision record are mandatory before an accepted correction becomes
   benchmark truth.
6. Freeze a ground-truth revision, then score. Never tune on the test results.

The scorer treats missing ground-truth facts as false negatives and extra model
facts as false positives. It reports device matching separately from field-level
micro precision/recall so a missing whole device cannot disappear inside an
average over matched cells.

## Vercel deployment boundary

The local workbench is file-backed. All workbench-specific source and deployment
configuration lives under `review_workbench/`. Its Vercel entry point mirrors
the immutable repository seed into `/tmp` and persists mutable review
state and uploaded PDFs in a private Vercel Blob store. PDFs stay out of Git.

Because the repository's main `pyproject.toml` contains the full extraction
stack, prepare the minimal workbench bundle before invoking the Vercel CLI:

```bash
.venv/bin/python review_workbench/prepare.py
vercel link --cwd review_workbench/.vercel-build --yes --project perla-ground-truth-review
vercel deploy --cwd review_workbench/.vercel-build
```

For a larger or more security-sensitive deployment, replace the compact Blob
state snapshot with these adapters:

- Postgres (for example Neon through the Vercel Marketplace): papers, users,
  memberships, review decisions, comments, issues, and immutable scoring runs.
- Private Vercel Blob: PDFs and optionally extracted page text. Serve PDFs
  through an authenticated application route rather than public URLs.
- An authentication provider: map the authenticated subject to a reviewer row;
  the present reviewer dropdown is not authentication.

Recommended tables are `papers`, `paper_versions`, `cells`, `field_facts`,
`field_reviews`, `comments`, `issues`, `extraction_runs`, and `score_runs`.
Use stable JSON Pointer paths for field reviews and preserve snapshots of the
ground truth and scoring configuration on every score run.

Relevant Vercel documentation:

- [Python runtime](https://vercel.com/docs/functions/runtimes/python)
- [Function runtimes and filesystem](https://vercel.com/docs/functions/runtimes)
- [Storage](https://vercel.com/docs/storage)
- [Private Blob storage](https://vercel.com/docs/vercel-blob/private-storage)
- [Postgres integrations](https://vercel.com/docs/postgres)
