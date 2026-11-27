# Readiness review — 25 November 2026

**The core is testable; the intended development-to-test study is not ready yet.**
This review traced input binding, extraction, scoring, resume/accounting and the
student entry points. It is not a claim that every legacy UI/export branch was audited.

## Before the paid study

| Gap | Evidence | Smallest next change |
| --- | --- | --- |
| Model-labeled development cannot run through the experiment command | `reference_io.load_reference` and `experiment_plan.prepare` require adjudicated reference directories; sealed teacher runs are rejected | Accept a sealed teacher extraction with source/model provenance, using the existing study schema; never invent human adjudication |
| Dev/test disjointness is a protocol, not an enforced input check | `reference_dataset.verify_dataset` records the role but does not compare a development roster with the test roster | Reject shared DOI or article/SI hashes before generation or scoring |
| The total campaign cap needs manual carry-forward between folders | Each `CampaignSpending` opens its output's `spending.json`; two independent $10 books can each reserve $6 | Use one campaign spending book across phases before unattended execution |

Development references have not been generated. The Astra configuration is an
uncalibrated candidate. Existing human references already informed earlier work;
calling them test data does not make the evaluation untouched.

## Fixed in this pass

- Direct dry runs now reject a token budget that cannot fit their first live request.
- Experiment JSON is read as UTF-8, including resume paths on Windows.
- CLI and TOML share one reasoning-level definition, including `max`.
  Supported provider values still depend on the selected model;
  [official OpenAI guidance](https://developers.openai.com/api/docs/guides/deployment-checklist)
  establishes support, not an accuracy gain.

## What the existing controls establish

Identity pairing excludes structured outcome values. Recovery and attribution use
the same fact inventory. Comparison intervals concern overall attributed fact F1, resampling papers;
per-field tables report their own counts. Failed/missing slots remain visible.
Sealed artifacts and unknown-cost reservations are checked on resume.

These are software guarantees. Teacher agreement, prior human-test exposure and
unverified annotation coverage still limit scientific claims. Keep field counts,
malformed claims, failures and denominators visible. Passing tests cannot establish
that extraction quality is sufficient for the paper.
