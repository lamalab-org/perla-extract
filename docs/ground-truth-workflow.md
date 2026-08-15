# Building extraction ground truth

Ground truth is a reviewed scientific dataset, not an edited model response. The model
provides useful pre-annotation; the protocol supplies the independent recall check,
evidence requirements, and adjudication needed to trust it.

## Evidence scope

The primary benchmark is `full_study`: claims explicitly reported in either the main
paper or its Supporting Information. Every evidence block records `source` and `page`,
so a future `main_only` evaluation can be projected from the same reviewed claims.

Never digitize plot traces, interpolate, infer unreported identities, or copy a value
from cited background literature. Record ambiguity in `unresolved_notes` when the
paper does not support a defensible value or link.

## Dataset lifecycle

Use three splits:

- `calibration` exposes schema, instructions, and interface problems. The first five
  papers belong here and must not be presented as final benchmark performance.
- `dev` supports prompt and workflow iteration.
- `test` stays locked until the extraction design is fixed. Each test paper is reviewed
  by at least two people and adjudicated.

Sample across publishers, SI length, table density, parser difficulty, device count,
architecture, stability reporting, and chemical complexity. Exclude reviews, news,
views, and perspectives before extraction.

## Review gates

1. **Blind inventory.** Before revealing candidates, search the main paper and SI and
   record expected counts for device families, individual devices, performance
   observations, population statistics, and stability tests.
2. **Identity.** Separate real variants, individual devices, measurement protocols,
   aggregates, and stability specimens. Accept equivalence only with positive source
   evidence.
3. **Scientific fields.** Check layer order, absorber composition, constituents,
   processing, performance, and stability. Preserve source qualifiers rather than
   coercing bounds, ranges, or approximate values into exact scalars. Mark every
   current rich record as verified or uncertain; editing a record invalidates its
   previous decision automatically.
4. **Evidence.** Every accepted addition or correction cites an exact supplied block.
   A removal records why the candidate is unsupported or ineligible.
5. **Completeness.** Repeat a paper-wide search for missing records and complete every
   quality gate.
6. **Adjudication.** Resolve reviewer disagreements before freezing a ground-truth
   revision.

The workbench rejects stale edits and validates the complete `StudyExtraction` after
every mutation. It preserves the immutable seed and append-only human event history.

## Evaluation unit

Report at least:

- device-family and individual-device inventory precision/recall;
- observation and stability-experiment inventory precision/recall;
- field-level exact or normalized agreement on correctly linked records;
- evidence-link validity;
- errors by reporting level: individual, champion, aggregate, stabilized, or unknown.

Do not let correct fields on matched devices hide an entirely missing device. Freeze
the source hashes, schema version, truth revision, model, prompt, parser, and scoring
configuration with every published run.
