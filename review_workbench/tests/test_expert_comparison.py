from __future__ import annotations

from review_workbench.expert_comparison import (
    ComparisonService,
    LocalComparisonStorage,
    build_comparison_source,
    neutral_records,
)


def historical_payload() -> dict:
    return {
        "cells": [
            {
                "perovskite_composition": {"formula": "MAPbI3"},
                "device_architecture": "nip",
                "pce": {"value": 18.2, "unit": "%"},
                "layers": [
                    {"name": "FTO", "functionality": "Substrate"},
                    {"name": "MAPbI3", "functionality": "Absorber"},
                ],
            }
        ]
    }


def empty_rich_payload() -> dict:
    return {
        "paper": {"title": "Example", "doi": "10.0000/example"},
        "device_families": [],
        "individual_devices": [],
        "performance_observations": [],
        "population_statistics": [],
        "stability_tests": [],
        "unresolved_notes": [],
    }


def source(reviewers: list[str] | None = None):
    return build_comparison_source(
        comparison_id="example-001",
        paper_id="10.0000--example",
        title="Example",
        split="dev",
        historical=historical_payload(),
        extracted=empty_rich_payload(),
        reviewer_ids=reviewers or ["ada", "grace"],
        randomization_seed="secret pre-registered seed",
        source_hashes={"main": "a" * 64},
    )


def preference_payload() -> dict:
    return {
        "active_seconds": 42,
        "preferences": {
            "factual_correctness": "A",
            "coverage_completeness": "B",
            "chemical_detail": "B",
            "record_relationships": "B",
            "evidence_traceability": "tie",
            "nomad_readiness": "B",
            "curation_effort": "B",
            "overall_preference": "B",
        },
        "confidence": 4,
        "rationale": "Candidate B needs less restructuring.",
    }


def test_comparison_balances_reviewers_and_keeps_origins_private(tmp_path):
    storage = LocalComparisonStorage(tmp_path)
    frozen = source(["ada", "grace", "linus", "margaret"])
    storage.create(frozen)
    service = ComparisonService(storage)

    assert sorted(item.blind_label for item in frozen.assignments) == [
        "A",
        "A",
        "B",
        "B",
    ]
    assert frozen.format_version == 2
    assert len(frozen.pairwise_rubrics) == 8
    assert {rubric.key for rubric in frozen.pairwise_rubrics} == set(
        preference_payload()["preferences"]
    )
    view = service.open("example-001", "ada")

    assert "origin" not in str(view)
    assert "historical_database" not in str(view)
    assert view["blind_label"] in {"A", "B"}
    assert view["review"]["revision"] == 1

    admin_view = service.list_for("unassigned-admin", include_unassigned=True)
    assert admin_view[0]["assigned"] is False
    assert admin_view[0]["batch_ready"] is False
    assert "origin" not in str(admin_view)


def test_neutral_projection_canonicalizes_rows_and_keeps_values_atomic():
    first = historical_payload()["cells"][0]
    first["additional_notes"] = '{"device_id":"internal","scientific":"not atomic"}'
    second = {"device_architecture": "pin", "voc": {"value": 1.1, "unit": "V"}}
    forward = neutral_records({"cells": [first, second]})
    reverse = neutral_records({"cells": [second, first]})

    assert forward == reverse
    claims = [field for record in forward for field in record.fields]
    assert any(field.value == 18.2 for field in claims)
    assert any(field.value == "%" for field in claims)
    assert not any(isinstance(field.value, (dict, list)) for field in claims)
    assert not any(field.path.endswith("additional_notes") for field in claims)
    assert len({field.field_key for field in claims}) == len(claims)


def test_final_submission_requires_every_claim_and_prevents_changes(tmp_path):
    storage = LocalComparisonStorage(tmp_path)
    frozen = source()
    storage.create(frozen)
    service = ComparisonService(storage)
    reviewer = next(
        item.reviewer_id
        for item in frozen.assignments
        if frozen.candidates[item.blind_label].common_payload.get("cells")
    )
    view = service.open("example-001", reviewer)
    fields = [field for record in view["records"] for field in record["fields"]]
    judgments = [
        {
            "field_key": field["field_key"],
            "verdict": "correct",
            "correction": "",
            "reference": None,
        }
        for field in fields
    ]

    result = service.save(
        "example-001",
        reviewer,
        {
            "revision": 1,
            "submit": True,
            "active_seconds": 93,
            "judgments": judgments,
            "missing_facts": [],
            "extra_records": 0,
            "missing_records": 0,
            "wrong_links": 0,
            "confidence": 4,
            "notes": "",
        },
    )

    assert result["submitted_at"] is not None
    assert service.list_for(reviewer)[0]["status"] == "native_utility_pending"

    try:
        service.save("example-001", reviewer, {"revision": 2})
    except ValueError as error:
        assert "cannot be changed" in str(error)
    else:
        raise AssertionError("submitted review was mutable")

    native = service.open_native("example-001", reviewer)
    assert native["candidate_sha256"]
    assert native["review"] is None
    utility = service.save_native(
        "example-001",
        reviewer,
        {
            "active_seconds": 30,
            "ratings": {
                "chemical_detail": 4,
                "relationships": 3,
                "verification_ease": 5,
                "nomad_usefulness": 4,
            },
            "suitable_as_curation_start": "yes",
            "notes": "Useful record links.",
        },
    )
    assert utility["suitable_as_curation_start"] == "yes"
    assert service.list_for(reviewer)[0]["status"] == "pairwise_preference_pending"

    pairwise = service.open_pairwise("example-001", reviewer)
    assert "origin" not in str(pairwise)
    assert set(pairwise["candidates"]) == {"A", "B"}
    assert pairwise["rubrics"] == [
        rubric.model_dump(mode="json") for rubric in frozen.pairwise_rubrics
    ]
    preference = service.save_pairwise(
        "example-001", reviewer, preference_payload()
    )
    assert preference["preferences"]["overall_preference"] == "B"
    assert service.list_for(reviewer)[0]["status"] == "complete"


def test_origin_reveal_is_blocked_until_all_reviews_are_final(tmp_path):
    storage = LocalComparisonStorage(tmp_path)
    storage.create(source())
    service = ComparisonService(storage)

    try:
        service.reveal("example-001")
    except ValueError as error:
        assert "every assigned review stage" in str(error)
    else:
        raise AssertionError("candidate identity was revealed before review")

    forced = service.reveal("example-001", force=True)
    assert set(forced["mapping"].values()) == {"historical_database", "new_extractor"}
    assert sorted(forced["incomplete_reviewers"]) == ["ada", "grace"]
