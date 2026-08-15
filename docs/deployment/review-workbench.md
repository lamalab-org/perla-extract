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
SI, and optional run configuration. It stores immutable seeds, compiled truth, event
history, evidence blocks, and manifests under the ground-truth directory.

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

The deployed adapter stores private PDFs under `papers/` and the mutable JSON state at
`workbench/study-review-state.json` in Vercel Blob. Configure
`BLOB_READ_WRITE_TOKEN`; the server-side token is never sent to the browser.

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
