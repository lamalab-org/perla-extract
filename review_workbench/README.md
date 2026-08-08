# Ground-truth review workbench

## Evidence boundary

Ground-truth cells and fields must be supported by information explicitly stated
in the main-paper prose, figure captions, or tables. Values inferred from plot
geometry and values available only in Supporting Information are intentionally
excluded, because the benchmark measures extraction from the main paper's
machine-readable text and tables.

The separate Figure audit records how much schema-relevant information is lost
under that policy. Count a figure as schema-relevant only when its values can be
encoded as a coherent record without tracing curves, estimating coordinates, or
joining unrelated points. A labelled inset table can qualify; a bare J-V curve
does not. Swarmplots with individual-device points but no cross-panel device
identity are counted separately as unlinked device statistics. Figure-only
counts and notes are coverage analysis and must not be promoted into the
ground-truth JSON.

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
highlight their source coordinates. A positioned text layer makes rendered PDF
text selectable; selected or suggested passages can be copied with their page
citation, and the complete extracted page text remains available as a fallback.
Automatic quote suggestions rank exact matches using field terminology, units,
and ancestor context such as composition, layer, and processing-step names.
Suggestions never mark a field as verified without reviewer action.
Missing/wrong-item reports can also carry a validated JSON Patch proposal. Each
proposal receives a 10-point readiness score covering its exact quote and page,
eligible source, device identity, measurement linkage, counterevidence, scope,
and guarded patch. Reviewers inspect old and proposed values in **Review
changes**, jump to the cited evidence, and accept, reject, defer, or load an
editable draft. Coupled operations are decided atomically. Acceptance validates
the complete result against the extraction schema, and all decisions retain the
reviewer, timestamp, selected changes, and note. Stale patches remain visible as
conflicts.
Use the **Open correction proposals** paper filter to work through this queue.

The primary queue is proposal-first: cited findings with concrete patches are
shown as **Ready to apply**, while ambiguous findings remain under **Needs a
stronger proposal**. Reviewers can select independent changes or atomic change
groups. The quantity scanner is retained
as an optional diagnostic reached from the Proposals view; raw unmatched
numbers are not treated as correction proposals because they lack reliable
device and measurement linkage.

Rerun the stored model extractions against any number of named ground-truth
revisions with the workbench-local scoring script. It emits fact-level micro
precision, recall, and F1 together with device coverage:

```bash
.venv/bin/python review_workbench/score_revisions.py \
  src/perla_extract/data/extractions \
  current=src/perla_extract/data/ground_truth/test
```

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
