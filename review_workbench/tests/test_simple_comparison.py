from __future__ import annotations

import copy
import json
from typing import Any

import fitz
import pytest

from review_workbench.api.index import BlobSimpleComparisonStorage
from review_workbench.server import ReviewApplication
from review_workbench.simple_comparison import (
    SimpleComparisonService,
    get_route,
    post_route,
)
from review_workbench.study_review import (
    RECORD_IDENTIFIERS,
    InventoryAuditRequest,
    MutationRequest,
    RecordDecisionRequest,
    StageRequest,
)

SPLIT = "calibration"
PAPER_ID = "10.0000--example"
QUOTE = "champion device"
ADMIN = {"id": "admin", "role": "admin"}
ADA = {"id": "ada", "role": "reviewer"}
GRACE = {"id": "grace", "role": "reviewer"}


def pdf_bytes(text: str) -> bytes:
    document = fitz.open()
    document.new_page().insert_text((72, 72), text)
    value = document.tobytes()
    document.close()
    return value


def seed_study(empty_study: dict) -> dict:
    study = copy.deepcopy(empty_study)
    study["device_families"] = [
        {
            "family_id": "family-control",
            "label": "Control",
            "variant": None,
            "architecture": None,
            "polarity": "not_reported",
            "full_stack_raw": None,
            "layers": [],
            "absorbers": [],
            "processing_steps": [],
            "evidence": [{"block_id": "main_p1_text_1", "quote": QUOTE}],
        }
    ]
    return study


def imported_app(tmp_path, empty_study, document_payload) -> ReviewApplication:
    app = ReviewApplication(tmp_path / "pdfs", tmp_path / "review")
    app.import_paper(
        SPLIT,
        PAPER_ID,
        pdf_bytes("The champion device reached a PCE of 24.1%."),
        json.dumps(seed_study(empty_study)).encode(),
        document_bytes=json.dumps(document_payload).encode(),
        reviewer_id="curator",
    )
    return app


def finalize(app: ReviewApplication) -> None:
    """Correct the seed once and adjudicate it through the public transitions."""

    store = app.store
    bundle = store.mutate(
        SPLIT,
        PAPER_ID,
        MutationRequest(
            action="replace",
            path="/device_families/0/full_stack_raw",
            value="ITO/perovskite/Ag",
            base_revision=store.revision(SPLIT, PAPER_ID),
            evidence=[{"block_id": "main_p1_text_1", "quote": QUOTE}],
        ),
        "curator",
    )
    bundle = store.inventory_audit(
        SPLIT,
        PAPER_ID,
        InventoryAuditRequest(
            base_revision=bundle["revision"],
            searched_sources=["main", "supplement"],
            expected_counts={},
        ),
        "curator",
    )
    bundle = store.complete_stage(
        SPLIT,
        PAPER_ID,
        StageRequest(stage="inventory", base_revision=bundle["revision"]),
        "curator",
    )
    for collection, identifier in RECORD_IDENTIFIERS.items():
        for record in bundle["ground_truth"].get(collection, []):
            bundle = store.decide_record(
                SPLIT,
                PAPER_ID,
                RecordDecisionRequest(
                    collection=collection,
                    record_id=record[identifier],
                    decision="verified",
                    base_revision=bundle["revision"],
                ),
                "curator",
            )
    for stage in ("fields", "completeness", "adjudication"):
        bundle = store.complete_stage(
            SPLIT,
            PAPER_ID,
            StageRequest(stage=stage, base_revision=bundle["revision"]),
            "curator",
        )


def request(**overrides: Any) -> dict[str, Any]:
    payload = {
        "comparison_id": "simple-1",
        "paper_id": PAPER_ID,
        "split": SPLIT,
        "reviewer_ids": ["ada", "grace"],
        "randomization_seed": "pre-registered seed",
        "paper": {"title": "Example study", "authors": ["Ada Lovelace", "Alan Turing"]},
        "extractor": {"kind": "seed"},
    }
    payload.update(overrides)
    return payload


def text_of(view: dict, label: str) -> str:
    prose = view["candidates"][label]["prose"]
    return " ".join(
        segment["text"]
        for section in prose["sections"]
        for paragraph in section["paragraphs"]
        for segment in paragraph["segments"]
    )


@pytest.fixture
def app(tmp_path, empty_study, document_payload) -> ReviewApplication:
    application = imported_app(tmp_path, empty_study, document_payload)
    finalize(application)
    application.create_simple_comparison(request())
    return application


def test_only_finalized_ground_truth_can_be_compared(
    tmp_path, empty_study, document_payload
):
    application = imported_app(tmp_path, empty_study, document_payload)

    with pytest.raises(ValueError, match="only finalized ground truth"):
        application.create_simple_comparison(request())


def test_reviewers_see_both_candidates_as_prose_without_their_origins(app):
    view = app.simple_comparisons.open("simple-1", "ada")

    texts = {label: text_of(view, label) for label in ("A", "B")}
    assert all(
        text.startswith("Lovelace and Turing in “Example study”")
        for text in texts.values()
    )
    assert sum("ITO/perovskite/Ag" in text for text in texts.values()) == 1
    rendered = json.dumps(view)
    for hidden in ("origin", "ground_truth", "extractor", "native", "family-control"):
        assert hidden not in rendered
    assert view["sources"] == ["main"]


def test_unassigned_reviewers_cannot_open_or_answer(app):
    with pytest.raises(PermissionError):
        app.simple_comparisons.open("simple-1", "mallory")
    with pytest.raises(PermissionError):
        app.simple_comparisons.save(
            "simple-1", "mallory", {"choice": "A", "justification": "Looks complete."}
        )


def test_a_choice_needs_a_justification_and_cannot_be_changed(app):
    with pytest.raises(ValueError, match="explain your choice"):
        app.simple_comparisons.save(
            "simple-1", "ada", {"choice": "A", "justification": "  ok  "}
        )
    with pytest.raises(ValueError):
        app.simple_comparisons.save(
            "simple-1", "ada", {"choice": "tie", "justification": "Both look fine."}
        )

    saved = app.simple_comparisons.save(
        "simple-1",
        "ada",
        {
            "choice": "A",
            "justification": "A reports the full stack.",
            "active_seconds": 42,
        },
    )

    assert saved["choice"] == "A" and saved["active_seconds"] == 42
    assert app.simple_comparisons.open("simple-1", "ada")["preference"]["choice"] == "A"
    with pytest.raises(ValueError, match="already submitted"):
        app.simple_comparisons.save(
            "simple-1", "ada", {"choice": "B", "justification": "Changed my mind."}
        )


def test_a_choice_is_rejected_when_the_texts_shown_have_changed(app):
    with pytest.raises(ValueError, match="changed since you opened it"):
        app.simple_comparisons.save(
            "simple-1",
            "ada",
            {
                "choice": "A",
                "justification": "A reports the full stack.",
                "candidate_hashes": {"A": "0" * 64, "B": "1" * 64},
            },
        )


def test_origins_are_revealed_only_after_every_reviewer_submitted(app):
    service = app.simple_comparisons
    view = service.open("simple-1", "ada")
    corrected = next(
        label for label in ("A", "B") if "ITO/perovskite/Ag" in text_of(view, label)
    )
    other = "B" if corrected == "A" else "A"
    service.save(
        "simple-1", "ada", {"choice": corrected, "justification": "Has the stack."}
    )

    with pytest.raises(ValueError, match="1 assigned reviewer"):
        service.export("simple-1")

    service.save(
        "simple-1", "grace", {"choice": other, "justification": "Shorter and correct."}
    )
    export = service.export("simple-1")

    assert export["origins"] == {corrected: "ground_truth", other: "extractor_seed"}
    assert export["preferred_by_origin"] == {"ground_truth": 1, "extractor_seed": 1}
    assert {item["chosen_origin"] for item in export["preferences"]} == {
        "ground_truth",
        "extractor_seed",
    }


def test_an_uploaded_run_can_be_the_extractor(tmp_path, empty_study, document_payload):
    application = imported_app(tmp_path, empty_study, document_payload)
    finalize(application)
    run = seed_study(empty_study)
    run["device_families"][0]["label"] = "Run family"

    application.create_simple_comparison(
        request(
            comparison_id="simple-run",
            extractor={"kind": "run", "extraction": run, "document": document_payload},
        )
    )

    view = application.simple_comparisons.open("simple-run", "ada")
    assert sum("Run family" in text_of(view, label) for label in ("A", "B")) == 1
    with pytest.raises(ValueError, match='kind must be "seed" or "run"'):
        application.create_simple_comparison(
            request(comparison_id="simple-bad", extractor={"kind": "magic"})
        )
    with pytest.raises(ValueError, match="already exists"):
        application.create_simple_comparison(request(comparison_id="simple-run"))


def test_routes_list_open_answer_and_restrict_admin_actions(app):
    service = app.simple_comparisons

    listed = get_route(service, ["api", "simple-comparisons"], ADA)
    assert [item["comparison_id"] for item in listed["comparisons"]] == ["simple-1"]
    assert get_route(
        service, ["api", "simple-comparisons"], {"id": "x", "role": "reviewer"}
    ) == {"comparisons": []}
    admin_list = get_route(service, ["api", "simple-comparisons"], ADMIN)["comparisons"]
    assert admin_list[0]["progress"] == {"submitted": 0, "assigned": 2}
    assert (
        get_route(service, ["api", "simple-comparisons", "simple-1"], ADA)["paper_id"]
        == PAPER_ID
    )
    assert get_route(service, ["api", "papers"], ADA) is None

    answer = {"choice": "B", "justification": "B is easier to verify."}
    saved = post_route(
        app,
        ["api", "simple-comparisons", "simple-1", "preference"],
        GRACE,
        lambda: answer,
    )
    assert saved["reviewer_id"] == "grace"

    with pytest.raises(PermissionError):
        post_route(
            app, ["api", "simple-comparisons"], ADA, lambda: request(comparison_id="x")
        )
    with pytest.raises(PermissionError):
        get_route(service, ["api", "simple-comparison-export", "simple-1"], ADA)
    assert post_route(app, ["api", "mutations", "a", "b"], ADA, dict) is None


class MemoryBlobStore:
    """Write-once semantics of Vercel Blob without network access."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def find(self, pathname: str) -> dict[str, str] | None:
        return {"pathname": pathname} if pathname in self.objects else None

    def list(self, prefix: str) -> list[dict[str, str]]:
        return [{"pathname": path} for path in self.objects if path.startswith(prefix)]

    def download(self, blob: dict[str, str]) -> bytes:
        return self.objects[blob["pathname"]]

    def put(
        self, pathname: str, body: bytes, content_type: str, *, overwrite: bool = True
    ):
        del content_type
        if not overwrite and pathname in self.objects:
            raise RuntimeError("blob already exists")
        self.objects[pathname] = body
        return {"pathname": pathname}


def test_blob_storage_keeps_sources_and_choices_write_once(app):
    source = app.simple_comparisons.storage.load_source("simple-1")
    storage = BlobSimpleComparisonStorage(MemoryBlobStore())  # type: ignore[arg-type]
    service = SimpleComparisonService(storage)

    storage.create(source)
    with pytest.raises(ValueError, match="already exists"):
        storage.create(source)
    service.save("simple-1", "ada", {"choice": "A", "justification": "A is complete."})

    assert storage.list_ids() == ["simple-1"]
    assert storage.load_source("simple-1") == source
    assert storage.load_preference("simple-1", "ada").choice == "A"
    with pytest.raises(ValueError, match="already submitted"):
        service.save(
            "simple-1", "ada", {"choice": "B", "justification": "B is complete."}
        )
