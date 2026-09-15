# Review and classify figures

The inventory includes a subfigure census for numbered main-text figures. It does not
census SI figures. Add one entry for every panel—for example, Figure 2a and Figure
2b—not one aggregate answer for the paper. A numbered figure may contain panels of
different types, such as a J–V curve and a device schematic.

For each panel, record its main figure number, optional panel label, PDF page, a short
description, and printed x- and y-axis labels. Choose one primary class:

- **J–V**;
- **EQE**, including integrated EQE;
- **population statistics**, including box, scatter, and violin plots;
- **stability**, when device performance is followed over time;
- **characterization**, such as IR, Raman, XPS, or XRD;
- **device structure**, including schematics and annotated microscopy; or
- **other**, including process diagrams.

Also record how numeric data are presented—explicit labels, an inset table, plotted
values, mixed, or none—and whether recovery is straightforward, partly
straightforward, requires digitization, not applicable, or uncertain. “Straightforward”
means that values are printed; it does not mean that points could be estimated from a
curve.

The app shows the coarse `StudyExtraction` destination implied by the selected figure
class—for example, performance metrics for a J–V panel or layers and absorbers for a
device schematic. This is orientation, not another annotation task: reviewers do not
map individual fields.

Mark a panel schema-relevant only when omitting it would leave a schema record or a
populated field incomplete. Sharing a scientific topic with the schema is not enough.
In particular, axis ticks, legend labels, and points that could merely be sampled from
a curve are not stored field values. Unreviewed proposals use a conservative default:
J–V, EQE, population, and stability panels are preselected only when they contain
explicit labels, an inset table, or mixed printed and plotted data; annotated device
structures may also be preselected. Characterization and other panels start outside
scope and require a reviewer to opt them in when they visibly contribute a specific
stored fact.

For a schema-relevant panel, count complete schema records and individual populated
fields that are visible there but absent from running text, captions, and tables. A
populated field is one stored fact, such as PCE, Voc, layer thickness, or test duration.
It is not a point sampled from a curve. When one fact spans multiple
panels, assign it to the single panel providing the clearest support so totals are not
duplicated. Do not add approximate visual readings to the text-evidenced ground truth.

The app presents these rows as a one-panel-at-a-time queue. **Confirm and next** marks
an unchanged suggestion as checked; editing any field marks it as corrected. Progress
counts only confirmed or corrected panels, and the server refuses to save a census
containing unchecked suggestions. Draft changes are retained in the reviewer's browser
for the same imported seed until the complete census is submitted. Arrow keys or
`J`/`K` move through the current filter and `V` confirms the open panel.

Paper-level figure, relevant-figure, figure-only-record, and figure-only-value totals
are derived from the panel rows. Older aggregate-only or panel-level censuses remain
readable, but their panels must be explicitly checked before an updated census can be
saved. The administrator feedback download includes `figure_panels.csv`, including
proposal identity and review status, and a JSON summary grouped by class, numeric
presentation, and extraction effort.

## Generate proposals from captions

Caption-grounded drafts can be generated without sending figures to a vision model:

```bash
python review_workbench/figure_census.py \
  --documents-dir results/review-v1 \
  --output review_workbench/review_app/figure-census-proposals.json \
  --model openrouter/openai/gpt-5.6-sol:exacto \
  --max-cost-usd 2
```

The command sends only main-text caption blocks, uses schema-constrained output, caches
validated responses, and rejects missing or invented caption identifiers. These rows
are suggestions rather than review events. The app pre-fills them only for a reviewer
who has no saved census, labels them as caption-derived, and persists them only after
the reviewer checks and saves the form. Because captions often omit axes and inset
contents, the generator must return uncertainty rather than infer what is visible.

## Render and inspect crops

Caption-only proposals cannot establish panel boundaries, axis labels, inset tables
or printed annotations. To inspect the images, first localize and render crops
without making a model request:

```bash
python review_workbench/figure_vision_batch.py \
  --runs-dir results/review-v1 \
  --output-dir results/figure-census \
  --proposal-output results/figure-census/render-report.json \
  --render-only
```

Each crop is tied to its PDF hash, page, rectangle, caption block, Docling version,
rendering settings, and image hash. Captions that cannot be localized unambiguously
are listed as failures for manual inspection; the code does not guess a rectangle.
Rerunning with the same inputs uses those verified crops.

The review app loads only the open paper's proposal and keeps every field editable.
When deterministic localization coordinates are available, it renders the active
subfigure crop directly from the stored PDF; no extra model call or external image
transfer occurs. Reviewers can also jump from the current panel to its main-paper page, add a missed panel,
remove an extra panel, correct its class, axes, presentation, or relevance, and enter
the verified figure-only record and populated-field counts. Filters expose unchecked,
uncertain, schema-relevant, or all panels. Captions without an automatic image match
are called out explicitly and must be added manually. A stable proposal identifier
keeps visual candidates attached when a reviewer corrects a figure or panel label.
Saving creates a reviewer event; it never mutates the static model proposal.

## Classify crops with a model

An optional image-capable model can propose panels and visibly printed values. This is
a separate, explicit command because it transmits figure crops to the configured model
provider:

```bash
python review_workbench/figure_vision_batch.py \
  --runs-dir results/review-v1 \
  --output-dir results/figure-census \
  --proposal-output review_workbench/review_app/figure-census-proposals.json \
  --model openai/gpt-5.6 \
  --max-model-calls 40 \
  --max-cost-usd 20
```

The batch checkpoints after every paper, shares one global call and cost budget, and
records per-paper failures without discarding completed work. The model may transcribe
only values visibly printed as annotations or inset-table cells. Axis ticks, sampled
curve points, and visually estimated coordinates are prohibited. A deterministic
text comparison marks whether each proposed value also occurs in extracted text, but
never declares an unmatched value “figure-only.” The reviewer sees the candidate and
makes that judgment while viewing the paper. Request logs retain image hashes and byte
counts rather than duplicating base64-encoded paper images.

## Evaluate classifications

After review, extract `figure_panels.csv` from the administrator feedback download and
score the frozen proposal:

```bash
python review_workbench/figure_census_evaluation.py \
  --proposal review_workbench/review_app/figure-census-proposals.json \
  --gold-csv feedback/figure_panels.csv \
  --reviewer-id REVIEWER_ID \
  --output results/figure-census/evaluation.json
```

The evaluator reports panel precision/recall/F1 first. Class, numeric-presentation,
recoverability, relevance, and axis-label agreement are calculated only for panels
whose paper, figure number, and panel label match exactly. If the CSV contains several
reviewers, selecting one or supplying a separately adjudicated CSV is mandatory; the
tool does not silently pool conflicting annotations.

The administrator feedback ZIP includes both `figure_census_proposals.json` (the exact
starting point shown in the app) and `figure_panels.csv` (current human annotations),
while the lossless event history preserves superseded edits and resets. Both the
original proposal and the corrected classifications remain available for comparison.

## Summarize figure-only information

After adjudication, calculate:

- record loss as `figure_only_records / (final_records + figure_only_records)`; and
- field-value loss as `figure_only_atomic_values /
  (final_populated_atomic_values + figure_only_atomic_values)`.

Here, `final_records` and `final_populated_atomic_values` count the adjudicated
text-evidenced reference, excluding the figure-only additions. If those additions
are already included in a combined reference, do not add them to its totals again.

These estimate the share of otherwise recoverable schema content excluded by a
text-only boundary. Separately,
`schema_relevant_figures / figures_reviewed` states how often main-text figures matter
at all.
