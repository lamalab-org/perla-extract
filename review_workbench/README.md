# Ground-truth review workbench

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
they are not included in Git or in the deployment bundle.

## Authentication

The deployed workbench uses Clerk email accounts when these variables are set:

- `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` and `CLERK_SECRET_KEY`
- `REVIEW_ADMIN_EMAILS`, a comma-separated administrator allowlist
- `REVIEW_USER_EMAILS`, a comma-separated reviewer allowlist

The API verifies Clerk session JWTs and derives authorship from the authenticated
user. Client-supplied reviewer IDs are ignored. Local file-backed use remains
available without Clerk variables.

To send or renew invitations without exposing the Clerk secret on the command
line, pull a Vercel environment to a protected temporary file and run
`invite_users.py --env-file <path> <emails...>`.
