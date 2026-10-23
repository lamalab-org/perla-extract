# Source-checked reference amendments

These amendments correct four studies in the frozen `ground_truth/v1` release.
They do not change that release in place. They are **not a new finalized reference
release**: publication of a new scoring release requires an export of the amended,
finalized app records. Do not point the scorer at this directory.

`finalization-plan.json` is the app's supported import format. Each change includes
the complete previous and replacement record; the app rejects a plan if the study
has changed. Original decisions, uploaded workbooks and earlier versions remain
in the private review history. The public plan contains scientific records and
source quotations, not reviewer identities or workbook comments.

## What changes

| Study | Amendment | What is deliberately not inferred |
|---|---|---|
| 10.1002/adfm.202212698 | Add Figure S11 to the supporting evidence for the existing PEACl Voc of 1.13 V. Its printed PEACl row resolves the inconsistent PEAI wording in the main text. | No value reversal or new Jsc transcription. No combination with a different champion. |
| 10.1016/j.nanoen.2022.107416 | Restore four qualitative additive-screening comparisons and the target/control steady-output comparison. Retain the S12 device-count wording without assigning five devices to the target arm. | No invented individual specimens, numerical distribution summaries or control-air endpoints. |
| 10.1021/acs.chemmater.8b01521 | Add the Au layer labelled above PEDOT in Figure 2B and Figure S5. Retain untreated and post-treated comparisons separately. | No electrode role, deposition process, thickness or pooled population mean. |
| 10.1038/s41467-023-36141-8 | Restore LiTFSI, TMA-Br and TEA-TFSI surface-screening populations from S4 and the bulk-mixed TEA-TFSI condition from S6. | No individual cells, sample counts or boxplot summaries. The S4 2 mg/mL IPA treatment is not the optimized TEA-TFSI recipe. |

The four additive comparisons and the paired steady-output result use
`PopulationStatistic(statistic_type="other")`: they are condition-level qualitative
comparisons, not numerical population estimates. This avoids asserting a physical
specimen link that the passages do not establish. The S4/S6 records use
`statistic_type="distribution"`, with no sample size or numerical outcome.

## Remaining representation limits

- `experiment-inventory.json` preserves the Nano Energy control-air experiment and
  its caption-supported conditions. The available SI has the caption but not the
  S12 plot. The schema requires an outcome-bearing stability checkpoint, so the
  experiment cannot be added as a `StabilityTest` without inventing an outcome.
  This inventory is **not included in the scorer's denominator**. The target's
  133-hour/80%-retention result must not be copied to the control.
- Control and 4A3HA recipes remain separate families in this batch. Collapsing them
  without a treatment-arm recipe representation would risk assigning 4A3HA to
  the control or losing its concentration. This is an unresolved grouping choice,
  not evidence for more experimentally independent architectures.
- The old extraction's `unresolved_notes` are not edited by the record-amendment
  interface. Some describe exclusions that these amendments supersede. They are
  not scored facts. Clean them through a history-preserving notes correction before
  freezing the next release; do not present those obsolete notes as new findings.
- Au is a **figure-only** fact. Its citations locate the images; the quoted captions
  do not themselves contain the Au label. An exact quote check verifies the locator,
  not the scientific interpretation. The addition was checked visually. Full-source
  scoring and text-only scoring must declare different input coverage explicitly.

## Verification

The private preparation validates every replacement against the saved evidence
document, replays the normal import/decision transitions, and asserts that previous
events are unchanged. The public tests check the frozen input hashes, guarded
replay, schema and links, exact expected additions, and absence of private metadata.
Self-scoring is checked separately; it tests scorer consistency, not scientific
truth. Neither passing validation nor a perfect self-score proves completeness.

Applying a plan can reopen earlier whole-study decisions because their content
hash changed. Inspect those cases; do not automatically approve every reopened
case merely to obtain a finalized export.
