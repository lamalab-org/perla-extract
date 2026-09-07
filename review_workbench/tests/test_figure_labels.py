from review_workbench.figure_labels import conservative_schema_relevance


def test_conservative_relevance_counts_directly_reported_schema_results():
    assert conservative_schema_relevance("jv", "explicit_numeric_labels") is True
    assert conservative_schema_relevance("stability", "inset_table") is True
    assert conservative_schema_relevance("eqe", "mixed") is True


def test_conservative_relevance_excludes_plots_that_require_inference():
    assert conservative_schema_relevance("jv", "plotted_values_only") is False
    assert conservative_schema_relevance("population_statistics", "uncertain") is False


def test_conservative_relevance_keeps_context_dependent_classes_opt_in():
    assert conservative_schema_relevance("device_structure", "no_numeric_data") is True
    assert (
        conservative_schema_relevance("characterization", "explicit_numeric_labels")
        is False
    )
    assert conservative_schema_relevance("other", "inset_table") is False
