# Ground-truth review workbench

## Evidence boundary

Ground-truth cells and fields must be supported by information explicitly stated
in the main-paper prose, figure captions, or tables. Values inferred from plot
geometry and values available only in Supporting Information are intentionally
excluded, because the benchmark measures extraction from the main paper's
machine-readable text and tables.

The separate Figure audit records how much schema-relevant information is lost
under that policy. Figure-only counts and notes are coverage analysis and must
not be promoted into the ground-truth JSON.

This directory contains the complete local review UI and its isolated Vercel
deployment surface. The scientific extraction and scoring code remains in
`src/perla_extract`.

Run the workbench locally from the repository root:

```bash
.venv/bin/python review_workbench/server.py \
  --pdf-dir /Users/kevinmaikjablonka/Downloads/test_eval_pdfs
```

Create the minimal deployment bundle and deploy it with the Vercel CLI:

```bash
.venv/bin/python review_workbench/prepare.py
vercel link --cwd review_workbench/.vercel-build --yes --project perla-ground-truth-review
vercel deploy --cwd review_workbench/.vercel-build --prebuilt
```

PDFs and mutable collaboration state are stored in private Vercel Blob storage;
they are not included in Git or in the deployment bundle. Reviewer identities,
comments, field evidence, and missing-item reports are runtime data and must not
be added to the ground-truth package.

Run the workbench-only tests independently with:

```bash
.venv/bin/pytest -q review_workbench/tests
```

## Authentication

For a small internal deployment, fixed email/password accounts can be supplied
without putting credentials in the repository:

- `REVIEW_INTERNAL_ACCOUNTS`, a JSON object keyed by normalized email address;
  each value contains `name`, `role`, and a PBKDF2 `password_hash`
- `REVIEW_SESSION_SECRET`, a random secret of at least 32 characters

`review_workbench.auth.hash_password()` creates compatible salted hashes. When
these variables are configured, the browser receives only a signed seven-day
session token; plaintext passwords and hashes stay server-side.

As a fallback, the deployed workbench can use Clerk email accounts when these
variables are set:

- `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` and `CLERK_SECRET_KEY`
- `REVIEW_ADMIN_EMAILS`, a comma-separated administrator allowlist
- `REVIEW_USER_EMAILS`, a comma-separated reviewer allowlist

The API verifies Clerk session JWTs and derives authorship from the authenticated
user. Client-supplied reviewer IDs are ignored. Local file-backed use remains
available without Clerk variables.

To send or renew invitations without exposing the Clerk secret on the command
line, pull a Vercel environment to a protected temporary file and run
`invite_users.py --env-file <path> <emails...>`.
