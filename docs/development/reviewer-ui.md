# Change the reviewer UI

The workbench uses native browser modules and a Python backend. No frontend build
step or framework is required. Install the repository development environment
(`pip install -e '.[dev]'`) and Node.js 20 or newer for the behavioral frontend tests.
These tests fail clearly if Node is missing; they are part of the normal pytest suite.

## Start with an invented paper

From the repository root, run:

```bash
python -m review_workbench.demo
```

Open the printed local URL and select **10.0000--synthetic-ui-demo**. This command generates
its own one-page source PDF and evidence blocks. It needs no PDFs, provider keys,
network access or model calls. The server uses the normal local administrator
identity and real review storage. Its temporary workspace disappears when you stop
the command with Ctrl-C; each launch starts fresh. Use `--port 8766` if necessary.

1. Open **2 · Review records**, choose **Individual devices** in the record-type
   filter, then select **Correct fields** for **Champion cell**.
2. Change **Linked device family** from Control to Alternative. The source quote is
   already selected. Save the correction, reload the page, and check the saved owner.
3. Change the record-type filter to **Performance observations** and open the reverse-scan observation. Correct its PCE reported value from `21%` to
   `20%` and its numeric value from `21` to `20`. Save and check **My work & undo**.
4. To check conflict recovery, open the same correction in two tabs. Save a change
   in one tab, then save the older draft in the other. It should offer to discard
   the old edit and load the latest version.

Use the source text in this example only for development: it is an invented
scientific record. Production operation and imports are described in the
[deployment guide](../deployment/review-workbench.md).

## Follow one correction through the code

| Responsibility | File | Change here when |
| --- | --- | --- |
| Scientific field types and relationship constraints | `src/perla_extract/study_extraction/models.py` | The stored scientific contract changes |
| Field controls, draft, JSON mode, schema defaults, stack editor | `review_workbench/review_app/record-editor.js` | A field needs a different control or editing behavior |
| Quick entry of J-V, stabilized, certified and EQE results as one row per performance observation | `review_workbench/review_app/jv-table.js` | Rows need other metrics, units or measurement types |
| Values, treatment context, measurement conditions and statistics shown on review cards | `review_workbench/review_app/record-details.js` | A stored scientific field must be visible before acceptance |
| Pure relationship choices and review calculations | `review_workbench/review_app/review-state.js` | Owner options or review calculations change |
| Authentication headers, endpoint methods, structured HTTP errors | `review_workbench/review_app/api-client.js` | The HTTP contract changes |
| Selected paper, saved bundle/revision, pending action and record save | `review_workbench/review_app/review-session.js` | A save or navigation transition changes |
| Page composition, evidence/PDF navigation and presentation messages | `review_workbench/review_app/app.js` | Screens need to connect differently |
| Dialog structure and styles | `review_workbench/review_app/index.html`, `styles.css` | Markup, accessibility or layout changes |
| Validation, audit history and persistence | `review_workbench/study_review.py` | Accepted mutations or revision semantics change |

The server returns the authoritative JSON Schema from `/api/study-schema` and
record identifiers/references in each paper bundle. The editor clones a record
into one draft; field controls and JSON mode update that draft. Invalid raw JSON
does not replace the last valid draft and cannot be saved. The editor exposes a
snapshot and the revision at which it opened. The session builds the mutation,
and adopts a new bundle only after the backend accepts it. HTTP failures carry
`status` and `code`; the page decides how to display them.

For a new scalar field, add its Python schema definition, then check the generated
draft and field control. Controls use schema types for nullable numbers and enums.
Top-level family/device links use `relationshipChoices` and the server identifier
map; record identity fields stay editable IDs. A new relationship kind or nested
relationship needs an explicit extension to that helper and editor, plus a test.
Keep scientific relationship constraints on the backend. The specialized device
stack editor lives with the other record controls.

## Run focused checks

```bash
node --version
python -m pytest -q review_workbench/tests/test_review_modules.py review_workbench/tests/test_review_state.py review_workbench/tests/test_demo.py
```

The module tests import real JavaScript functions and cover draft isolation,
owner selection, field/JSON switching, reordering, request errors, and stale-save
behavior. The demo test saves and reloads an owner correction through the real
backend. Keep assertions on behavior rather than function placement or copied
source snippets. After changing a user journey, repeat it in the synthetic UI;
the lightweight DOM test fixture does not establish browser layout or accessibility.

New browser modules must be included in `server.py`'s static-asset allowlist.
`prepare.py` copies the complete `review_app` directory into deployment bundles.

Optional nested objects have **Add** and **Clear** controls. Lists have item controls
that respect schema minimum/maximum lengths; ordered items retain their sequence.
New nested values receive the selected citation when saved, while existing citations
stay intact. Check that this passage supports each added value before saving.
