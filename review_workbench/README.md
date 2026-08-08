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

Research papers are not excluded because they contain or focus on tandem
devices. Tandem device records themselves are outside the current schema and
must be omitted from ground truth, while explicitly reported single-junction
devices from the same paper remain eligible for extraction and scoring. A
tandem-only research paper therefore has an empty `cells` list rather than an
article-level exclusion.

Field reviews record whether a reported value is exact, approximate, a lower or
upper bound, or a range, independently of whether it is a single measurement,
mean, median, champion, stabilized value, or distribution. When the remaining
uncertainty or device linkage still cannot be represented faithfully, log a
`Schema limitation / uncertainty` issue. These examples should inform a later
schema revision instead of being coerced into exact scalar ground truth.

Seed the curated initial findings into private Vercel Blob state after pulling
the production environment. The operation is idempotent and does not add issue
records to Git:

```bash
PYTHONPATH=. .venv/bin/python review_workbench/seed_review_findings.py --dry-run
PYTHONPATH=. .venv/bin/python review_workbench/seed_review_findings.py
```

This directory contains the complete local review UI and its isolated Vercel
deployment surface. The scientific extraction and scoring code remains in
`src/perla_extract`.

The paper rail shows split-wide progress for every reviewer, including fields
reviewed and papers completed. Within a paper, **Next pending** advances through
the current reviewer's remaining fields. PDF search results jump to and
highlight their source coordinates; the viewer can copy either the matched
passage with its page citation or the complete extracted text of the page.

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
