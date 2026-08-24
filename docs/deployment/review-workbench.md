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
  --runs-dir study_extraction/calibration-v4 \
  --pdf-dir review_data/pdfs \
  --review-data review_data/current \
  --split calibration
```

The command refuses incomplete runs and any run with unresolved evidence-validation
issues. Existing review items are still protected by the immutable-seed storage
contract, so rerunning it cannot silently replace a seed.

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
checklist. Scalar corrections are grouped into separate sheets for the scientific
record types present in that paper, such as **Device Families**, **Individual
Devices**, **Performance Observations**, **Population Statistics**, and **Stability
Tests**. Both the checklist and correction tabs begin with readable record, family,
and device context followed by the stable IDs. **Individual device** means the schema
contains an explicit device link. **Device family only** deliberately does not imply
that any listed individual device belonged to the reported population. Each row
remains one atomic schema value with its JSON path and nearest citation. Yellow cells
are editable, and rows may be sorted or filtered. Identifiers, relationship context,
and row membership are intentionally read-only.

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

Before introducing a regenerated dataset, copy the previous production state to a
versioned private Blob path and verify its digest. Keep the old `papers/` prefix
read-only; when a regenerated item with the same paper ID uses different PDF bytes,
also preserve the old PDF below the versioned legacy path. Import the new records into
a separate split such as `calibration`; do not overwrite the legacy state object or
reuse an existing rich review item. This keeps historical drafts recoverable while
preventing reviewers from comparing flat and rich records as equivalent annotations.

Review state is committed under `state/`. One immutable source bundle contains the
seed, evidence document, manifest, and initial revision. Each saved human change writes
one new revision snapshot containing both the validated truth and complete event
history. The familiar `seeds/`, `events/`, `documents/`, `manifests/`, and split
directories are refreshed as derived, inspectable exports.

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
annotations** saves the same reviewer-scoped ledger
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
