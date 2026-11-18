# Proposed volume correction

`proposal.json` uses the existing review workbench `PlanRequest` format and binds
the change to revision 34 of `10.1038--nmat4014` in release v2. The two volume
units are **µl**, as shown in Methods on printed pages 902–903. Only their
`raw_value` and `unit` fields change. Original evidence strings are retained
because their text extraction also lost the micro glyph; the PDF is authoritative.

Source PDF SHA-256: `d77f1a12b1d895e2802cb38974988bb020af680590a77699435b1c46486a7250`.

Status: assistant-inspected, independent adjudication pending. This is a proposed
amendment for sensitivity analysis, not a new independently reviewed benchmark.
The archived release is unchanged. Apply through the existing adjudication workflow
after source review; do not mark coverage complete from this correction.
