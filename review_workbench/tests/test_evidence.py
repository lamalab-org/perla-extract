from review_workbench.review_evidence import (
    disagreement_paths,
    fact_suggestions,
    flatten_facts,
    quantity_mentions,
    reconcile_evidence,
    review_progress,
    reviewer_entry,
    save_evidence,
)


def test_field_evidence_tracks_reviewers_independently():
    truth = {"cells": [{"pce": {"value": 20.1, "unit": "%"}, "layers": []}]}
    evidence = reconcile_evidence("paper", truth)
    field = evidence["fields"]["/cells/0/pce/value"]
    field["reviews"] = {
        "alice": {"status": "verified", "page": 3, "quote": "PCE of 20.1%", "notes": ""},
        "bob": {"status": "incorrect", "page": 3, "quote": "", "notes": "Champion vs average"},
    }

    assert reviewer_entry(field, "alice")["status"] == "verified"
    assert review_progress(evidence, "alice")["reviewed"] == 1
    assert disagreement_paths(evidence) == ["/cells/0/pce/value"]


def test_field_evidence_persists_relation_and_aggregation(tmp_path):
    truth = {"cells": [{"pce": {"value": 20.1, "unit": "%"}}]}
    evidence = reconcile_evidence("paper", truth)
    evidence["fields"]["/cells/0/pce/value"]["reviews"] = {
        "alice": {
            "status": "verified",
            "page": 2,
            "quote": "average PCE above 20.1%",
            "notes": "",
            "value_relation": "lower_bound",
            "aggregation": "mean",
        }
    }

    saved = save_evidence(tmp_path, "test", "paper", truth, evidence)
    review = reviewer_entry(saved["fields"]["/cells/0/pce/value"], "alice")

    assert saved["schema_version"] == 2
    assert review["value_relation"] == "lower_bound"
    assert review["aggregation"] == "mean"


def test_suggestions_and_unmapped_quantities():
    truth = {"cells": [{"pce": {"value": 20.1, "unit": "%"}, "layers": []}]}
    facts = flatten_facts(truth)
    pages = (
        "The champion device reached 20.1% PCE at 1.12 V. A second device reached 18.4%.",
    )

    suggestions = fact_suggestions(pages, facts)
    mentions = quantity_mentions(pages, facts)

    assert suggestions["/cells/0/pce/value"]["page"] == 1
    assert any(item["text"] == "20.1%" and item["mapped_paths"] for item in mentions)
    assert any(item["text"] == "1.12 V" and not item["mapped_paths"] for item in mentions)
    assert any(item["text"] == "18.4%" and not item["mapped_paths"] for item in mentions)
