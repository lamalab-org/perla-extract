# Model-generated development set

Use **separate papers** and the strongest extraction pipeline we can support to
create development references. [astra.toml](astra.toml) is a candidate teacher
configuration; it has not been calibrated. Development references are not yet generated.

1. Commit a development roster disjoint from the [human test papers](../test/papers.json), including every article/SI hash.
2. Check reasoning/output headroom and refinement on these development papers. Do not consult human test scores to choose settings.
3. Freeze the teacher's source-linked extractions and their configurations as **model-generated references**.
4. Tune cheaper models and pipeline settings against those references. This measures teacher agreement, not human-validated accuracy.
5. Freeze the selected pipelines and scorer, then run the [human test evaluation](../test).

Use the existing extraction schema and scorer. Keep model provenance explicit;
model-generated references must not be marked human-reviewed. See the
[study protocol](../STUDY.md) for scoring and budget rules.

Teacher-reference loading and enforced dev/test overlap checks remain to implement;
see the [readiness review](../../docs/development/readiness.md).
