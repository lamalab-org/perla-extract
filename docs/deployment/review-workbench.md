<!-- generated-by: gsd-doc-writer -->
# Review workbench

The review workbench is a separate browser application for turning rich model
extractions into versioned ground truth. It reviews composition, layers, processing,
performance, population statistics, stability, and explicit identity links without
modifying the immutable model seed.

For the scientific protocol, start with [Build ground truth](../workflows/ground-truth-review.md).

## Run locally

From the repository root:

```bash
python review_workbench/server.py \
  --pdf-dir review_pdfs \
  --ground-truth-dir review_data
```

Open <http://127.0.0.1:8765>. The local server uses a local administrator identity.
`--host` and `--port` change the listening address; the defaults are `127.0.0.1` and
`8765`.

The application imports `extraction.json`, `document.json`, the main PDF, an optional
SI, and optional run configuration. Import `coverage_audit.json` and
`refinement_audit.json` as well when they are available. Import
`targeted_repair.json` to show the evidence-local recovery worklist and whether its
patch passed the quality gates. Import `enrichment.json` to
show source-reported absorber composition beside proposed A/B/X assignments, including
their acceptance status and issues. These files remain provenance aids:
after the blind census is submitted, the interface highlights unmatched inventory
candidates and the record groups changed by refinement so reviewers can focus their
attention without treating any model artifact as truth. The app stores immutable
seeds, compiled truth, event history, evidence blocks, and manifests under the
ground-truth directory.

For a validated batch, use the same import contract non-interactively:

```bash
python review_workbench/import_runs.py \
  --runs-dir study_extraction/calibration-v4 \
  --pdf-dir review_data/pdfs \
  --review-data review_data/current \
  --split calibration
```

The command refuses incomplete runs and any run with unresolved evidence-validation
issues. Existing review items are still protected by the immutable-seed storage
contract, so rerunning it cannot silently replace a seed.

## Review records efficiently

After the blind census, the Records tab presents a device-centered queue rather than
one long list. A family is followed by its population records, devices, observations,
and stability tests so shared architecture, stack, absorber, and composition remain in
view. The first cited source block opens in the paper automatically.

The default **Remaining** view removes verified and uncertain records as the reviewer
advances. **Needs attention** limits the queue to requested corrections, records
changed by the quality pass, and composition proposals that need review. Use `V` to
verify, `U` for uncertain, `C` to correct, and the arrow keys or `J`/`K` to move between
records. Corrections open a field-oriented editor with existing evidence preselected;
complete JSON remains available only as an advanced escape hatch.

The study header compares the immutable seed's schema version and generated schema
hash with the running extractor. Older seeds that remain structurally readable are
not silently presented as current outputs: the interface warns that newly introduced
fields still need regeneration or explicit human review.

Before introducing a regenerated dataset, copy the previous production state to a
versioned private Blob path and verify its digest. Keep the old `papers/` prefix
read-only; when a regenerated item with the same paper ID uses different PDF bytes,
also preserve the old PDF below the versioned legacy path. Import the new records into
a separate split such as `calibration`; do not overwrite the legacy state object or
reuse an existing rich review item. This keeps historical drafts recoverable while
preventing reviewers from comparing flat and rich records as equivalent annotations.

Review state is committed under `state/`. One immutable source bundle contains the
seed, evidence document, manifest, and initial revision. Each accepted change writes
one new revision snapshot containing both the validated truth and complete event
history. The familiar `seeds/`, `events/`, `documents/`, `manifests/`, and split
directories are refreshed as derived, inspectable exports.

Every authenticated reviewer can open **My annotations** from the header. This view
reads the persisted revision log across the selected split and shows only that
reviewer's census submissions, record decisions, corrections, evidence, notes, and
stage completions. Decisions are marked current or superseded when later edits change
the reviewed record. **Download my annotations** saves the same reviewer-scoped ledger
as readable JSON, including exact before/after values and revision timestamps. It is a
personal progress export and is deliberately separate from adjudicated ground truth.

After the final administrator adjudication, **Download PR bundle** produces a
deterministic ZIP containing the rich ground truth, immutable seed, complete review
events, and provenance manifest. The download is disabled if any edit occurred after
adjudication or if source-evidence validation fails. For a local review directory, the
equivalent tracked export command is documented in
[Build ground truth](../workflows/ground-truth-review.md#freeze-a-revision-for-a-data-pr).

## Verify the application

```bash
python -m pytest -q review_workbench/tests
```

## Prepare a Vercel deployment

The preparation command creates a minimal bundle at
`review_workbench/.vercel-build`. Papers and ground truth are not copied into that
bundle.

```bash
python review_workbench/prepare.py
vercel link --cwd review_workbench/.vercel-build --yes \
  --project perla-ground-truth-review
vercel deploy --cwd review_workbench/.vercel-build --prebuilt
```

The deployed adapter stores new private PDFs under
`workbench/review-pdfs/<split>/`, immutable source bundles under
`workbench/review-sources/`, and immutable revision snapshots under
`workbench/review-revisions/` in Vercel Blob. Split-scoped PDF paths prevent a newly
generated calibration item from silently reusing a historical PDF with the same DOI.
The older `papers/` prefix remains a read-only fallback for legacy deployments.
Creating revision `N + 1` with overwrite disabled is the compare-and-swap operation:
if two serverless instances review revision `N`, exactly one can create the next path
and the other receives a stale revision error. No process-local lock or mutable
whole-dataset blob is involved.

Configure `BLOB_READ_WRITE_TOKEN`; the server-side token is never sent to the browser.

## Authentication

Choose one authentication mode.

### Fixed internal accounts

| Variable | Purpose |
| --- | --- |
| `REVIEW_INTERNAL_ACCOUNTS` | JSON object keyed by reviewer email with name, role, and PBKDF2 password hash |
| `REVIEW_INTERNAL_ACCOUNT_ADDITIONS` | Optional JSON object merged into the primary account list |
| `REVIEW_SESSION_SECRET` | At least 32 characters; signs seven-day sessions |

An account role is `reviewer` unless it is explicitly `admin`.

### Clerk

| Variable | Purpose |
| --- | --- |
| `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` | Browser-safe Clerk publishable key |
| `CLERK_SECRET_KEY` | Server-side Clerk API key |
| `REVIEW_USER_EMAILS` | Comma-separated invited reviewers |
| `REVIEW_ADMIN_EMAILS` | Comma-separated administrators |

Invite named users with:

```bash
python review_workbench/invite_users.py \
  reviewer@example.org admin@example.org \
  --admin admin@example.org
```

The server checks the allowlist after validating the Clerk session. Keep all secret
values in deployment configuration or a local ignored env file; do not commit them.
