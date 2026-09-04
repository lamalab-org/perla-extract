<!-- generated-by: gsd-doc-writer -->
# Review workbench

The review workbench is a separate browser application for turning rich model
extractions into versioned ground truth. It reviews composition, layers, processing,
performance, population statistics, stability, and record relationships without
modifying the immutable model seed.

For the scientific protocol, start with [Build ground truth](../workflows/ground-truth-review.md).
The separate [blinded extractor comparison](../workflows/expert-comparison.md) uses
the same authentication and PDFs but writes an independent immutable experiment log;
comparison answers never alter ground truth.

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
SI, and optional run configuration. Import `claim_coverage_audit.json` and
`refinement_audit.json` as well when they are available. Import
`targeted_repair.json` to show the evidence-local recovery worklist and whether its
patch passed the quality gates. Import `enrichment.json` to
show source-reported absorber composition beside proposed A/B/X assignments, including
their acceptance status and issues. These files remain provenance aids. The Records
tab is available immediately, while the Census tab records corrected paper-level
totals and the main-text figure gap. After the census is saved, the interface
highlights count differences and the record groups changed by refinement
so reviewers can focus their attention without treating any model artifact as truth.
The app stores immutable seeds, compiled truth, event history, evidence blocks, and
manifests under the ground-truth directory.

The figure census is deliberately limited to numbered figures in the main paper. It
records how many figures contain schema-relevant content and how many schema records or
atomic values are available only from those figures. The app records all imported
sources as the record-search scope; reviewers do not toggle main/SI checkboxes,
because those flags are not measurements of figure-extraction loss.

For a validated batch, use the same import contract non-interactively:

```bash
python review_workbench/import_runs.py \
  --runs-dir study_extraction/review-seeds \
  --pdf-dir review_data/pdfs \
  --review-data review_data/current \
  --split dev
```

The command refuses incomplete runs and any run with unresolved evidence-validation
issues. Existing review items are still protected by the immutable-seed storage
contract, so rerunning it cannot silently replace a seed.

To seed the deployed Vercel dataset from the same validated runs, first inspect the
exact missing set and then repeat without `--dry-run`:

```bash
python -m review_workbench.import_vercel_runs \
  --manifest data/study_extraction/cohorts/review-v1.json \
  --runs-dir results/review-v1 \
  --pdf-dir /path/to/main-papers \
  --env-file review_workbench/.vercel-build/.vercel/.env.production.local \
  --split dev \
  --dry-run
```

This command imports only included manifest papers with absent IDs. Exclusions and
unrelated run directories are ignored, and it cannot replace an immutable seed or any
human revision already stored for an existing paper.

After approving a regenerated batch, add `--refresh-existing`. The command still
refuses incomplete or evidence-invalid runs. For each included existing paper, it
stores the run's `document.json` as a new immutable evidence version and appends a
ground-truth revision bound to it; identical papers are no-ops. Run with `--dry-run`
first to see how many papers would change:

```bash
python -m review_workbench.import_vercel_runs \
  --manifest data/study_extraction/cohorts/review-v1.json \
  --runs-dir results/review-v1 \
  --pdf-dir /path/to/main-papers \
  --env-file review_workbench/.vercel-build/.vercel/.env.production.local \
  --split dev \
  --reviewer-id administrator@example.org \
  --refresh-existing \
  --dry-run
```

## Review records efficiently

The Records tab is available before or after the census and presents a device-centered
queue rather than one long list. A family is followed by its population records, devices, observations,
and stability tests so shared architecture, stack, absorber, and composition remain in
view. Reviewers may inspect and correct records while compiling the census. The final
Completeness tab remains locked until the census is saved. The first cited source block
opens in the paper automatically. This is a model-assisted correction workflow, not a
blind recall measurement; the separate figure census measures the specific text-only
loss the benchmark is intended to quantify.

The selected record is the primary review surface; related device context appears
after it as collapsible supporting information. For stability tests, the workbench
shows the specimen link, every test-wide condition, and every checkpoint's time,
checkpoint-specific conditions, and outcomes. Atomic values are not collapsed into a
single summary: each value shows its raw wording, numeric normalization, JSON path,
and direct evidence action.

The default **Remaining** view removes records marked **All fields match source** or
**Cannot establish** as the reviewer advances. **Needs attention** limits the queue to records the reviewer marked for
correction, records added or revised during the model's second evidence read, and A/B/X
assignments that did not pass every automated check. These are review priorities, not
correctness judgments. Every displayed reason includes a plain-language explanation.
Use `V` for **All fields match source**, `U` for **Cannot establish**, `C` to correct,
and the arrow keys or `J`/`K` to move between records. The decision applies to the
complete selected record, not only its first number or related device context.
Corrections open with existing evidence preselected. Reviewers can switch
directly between the field-oriented editor, where every label includes its JSON
Pointer, and the complete JSON for that record. Opening **Correct fields** does not
save a decision or wait for a server round trip; only the submitted correction creates
a revision. Removal is a separate action. Its dependency explanation appears only
after **Remove extra record** is chosen and names the linked record types that must be
reassigned or removed first.

Reviewers who prefer a spreadsheet can use **Download Excel** for the whole paper,
or download a smaller workbook for the device currently in context from the record
queue. The device workbook includes its family, individual device, linked performance
observations, family population statistics, and stability tests explicitly linked to
that device or only to its family. The short **Record review** sheet is the primary
checklist: its outcome and note columns come first, records are grouped by their
explicit family and device links, and family-only statistics remain visibly distinct
from measurements of an individual device. Reviewers do not need to inspect every
flattened scalar row. They open a detail sheet only when a record contains a wrong
value. Scalar corrections are grouped into separate sheets for the scientific
record types present in that paper, such as **Device Families**, **Individual
Devices**, **Performance Observations**, **Population Statistics**, and **Stability
Tests**. Both the checklist and correction tabs begin with readable record, family,
and device context; importer-only identity columns remain in the file but are hidden.
Yellow cells are the only reviewer inputs, while structural identity rows are hidden
because complete-record additions, removals, and relinking stay in the browser.
**Individual device** means the schema
contains an explicit device link. **Device family (no individual device link)** deliberately does not imply
that any listed individual device belonged to the reported population. Each row
remains one atomic schema value with its JSON path and nearest citation. Yellow cells
are editable, and rows may be sorted or filtered. Relationship context and row
membership are intentionally read-only.

Uploading the returned workbook checks its paper, schema hash, original truth digest,
and revision; reconstructs the expected rows; validates every correction citation;
and validates the complete `StudyExtraction`. All corrections and decisions are then
saved together as one attributable revision. A stale or structurally changed workbook
is rejected. The import appears in **My edits & undo** and can be reversed while none
of its corrected records has since changed. Excel does not add or delete complete
records; those structural changes stay in the browser so links remain explicit.

The study header compares the immutable seed's schema version and generated schema
hash with the running extractor. Older seeds that remain structurally readable are
not silently presented as current outputs: the interface warns that newly introduced
fields still need regeneration or explicit human review.

An administrator may replace an active pre-annotation with a regenerated extraction.
The refresh validates the extraction against the regenerated `document.json`, stores
that parsed document as a new immutable evidence version, and appends a revision that
points to it. It never rewrites the seed, an earlier evidence document, or an earlier
review revision. Changed record digests reopen stale reviewer decisions automatically.
Use a separate split only when the papers, schema boundary, or review protocol differ;
a routine extractor improvement belongs in the existing split as an audited refresh.

Review state is committed under `state/`. One immutable source bundle contains the
seed, initial evidence document, manifest, and initial revision. A regenerated parser
document is stored once as another immutable evidence version. Each review revision
records which version supports its citations; later human edits inherit that binding.
The familiar `seeds/`, `events/`, `documents/`, `manifests/`, and split directories are
refreshed as derived, inspectable exports of the active revision.

Every authenticated reviewer can open **My edits & undo** from the header. **Current
work** is the default view: it groups active decisions, census state, completed stages,
and safely reversible scientific edits by paper, with direct actions to continue the
paper or reset only that paper. **History** contains the complete reviewer-specific
revision log. Decisions are attached to exact event IDs, so an older decision cannot
appear current merely because a later decision chose the same outcome. **Reset all
current progress** clears the reviewer's current record decisions, census, and
completed stages across the selected dataset. Both reset actions write compensating
events, so prior activity remains inspectable instead of being deleted. Corrections and
workbook imports that are still untouched offer
**Undo this saved edit**. Undo writes a linked, validated revision instead of deleting history; the
action is unavailable once later work changes the same value. **Download my
annotations** saves the same reviewer-scoped ledger as readable JSON, including exact
before/after values and revision timestamps. It is a personal progress export and is
deliberately separate from adjudicated ground truth.

Administrators also see **Download feedback** in the header. It produces one ZIP for
the complete deployment rather than requiring a paper-by-paper download:

- `feedback.json` is the lossless snapshot, with immutable event history and the
  current decisions, census answers, and completion stages derived from it;
- `review_events.csv` is one row per saved ground-truth review action and explicitly
  marks edits that were later undone;
- `comparison_reviews.csv` contains blinded extractor-study responses, native-output
  utility ratings, and dimension-specific A/B preferences. Candidate origins remain
  blank until every assigned review stage in that comparison is final;
- the comparison batches inside `feedback.json` include the exact frozen rubric
  questions, minimum acceptable bars, and preference rules shown to reviewers;
- `README.txt` explains the files and how resets, drafts, and superseded answers are
  represented.

Every authenticated Excel submission within the documented 15 MiB limit is retained
byte-for-byte under `uploaded_workbooks/` before validation begins. This includes
accepted, rejected, stale, comment-only, and no-op files. `feedback.json` contains an
immutable receipt with its filename, paper, reviewer, timestamp, size, and SHA-256,
plus a separate accepted or rejected validation outcome. If archival fails, the app
does not process the review. Earlier uploads cannot be reconstructed as identical
files because the previous deployment did not retain their bytes.

Excel cell comments and standalone text in a **Reviewer note** cell are review
feedback too. A comment-only workbook is accepted, archived, and represented in the
event history with its sheet, cell, kind, author, text, and record or schema-path
context when available. If a reviewer still has a commented workbook based on an
older paper revision, uploading it recovers and archives the comments but
deliberately does not apply stale value edits.

The export contains stable reviewer IDs and scientific review content, but no PDFs,
passwords, session tokens, or deployment configuration. Treat it as research data: it
may contain reviewer-written notes and should be stored with the same access controls
as the ground-truth dataset.

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
vercel pull --cwd review_workbench/.vercel-build --yes --environment production
vercel build --cwd review_workbench/.vercel-build --yes --prod
vercel deploy --cwd review_workbench/.vercel-build --prebuilt --prod
```

The deployed adapter stores new private PDFs under
`workbench/review-pdfs/<split>/`, immutable source bundles under
`workbench/review-sources/`, and immutable revision snapshots under
`workbench/review-revisions/` in Vercel Blob. Split-scoped PDF paths prevent a newly
generated calibration item from silently reusing a historical PDF with the same DOI.
The older `papers/` prefix remains a read-only fallback for legacy deployments.
The deployed paper rail reads only source and revision pathnames. It does not download
every full extraction merely to show the list; the selected study is fetched on demand.
The browser keeps the last authenticated paper list as a fast-start cache and refreshes
it against Blob storage.
The progress dialog reuses its last response for the current browser session while a
fresh response loads. Current paper revisions are fetched concurrently, and an existing
revision is read without redundantly downloading its immutable source bundle.
Creating revision `N + 1` with overwrite disabled is the compare-and-swap operation:
if two serverless instances review revision `N`, exactly one can create the next path
and the other receives an HTTP 409 conflict. The reviewer is asked to load the latest
saved version and reconsider their change; exact revision numbers are kept in server
logs for diagnosis. No process-local lock or mutable whole-dataset blob is involved.

Configure `BLOB_READ_WRITE_TOKEN`; the server-side token is never sent to the browser.

## Authentication

Configure either authentication mode, or both during migration. When both are present,
reviewers can keep using their existing project password while migrated accounts gain
Clerk's email-code password recovery. The sign-in screen exposes both choices without
changing reviewer identities or saved annotations.

### Fixed internal accounts

| Variable | Purpose |
| --- | --- |
| `REVIEW_INTERNAL_ACCOUNTS` | JSON object keyed by reviewer email with name, role, and PBKDF2 password hash |
| `REVIEW_INTERNAL_ACCOUNT_ADDITIONS` | Optional JSON object merged into the primary account list |
| `REVIEW_INTERNAL_ACCOUNT_OVERRIDES` | Optional final account layer for an independently deployable password rotation |
| `REVIEW_INTERNAL_ACCOUNT_LAYER_*` | Optional named JSON layers, applied alphabetically after the legacy layers, so later password resets can be deployed without replacing write-only secrets |
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

In production, email recovery is enabled only when the Clerk publishable key begins
with `pk_live_`. Development (`pk_test_`) instances remain usable in local and preview
deployments but are deliberately ignored by the production workbench.

With both production-ready sets of variables configured, **Forgot your password? Use email recovery**
opens Clerk's prebuilt sign-in form. The reviewer chooses **Forgot password**, receives
a code at the verified email address, and sets a new password there. Fixed internal
passwords remain available only as a migration fallback; the application never emails,
stores, or logs a plaintext password itself.
