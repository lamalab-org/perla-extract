"""Protect the experiment against plausible scientific and bookkeeping mistakes."""

import json
import subprocess

import pytest

from examples.parsing.compare import (
    audit,
    conversion_complete,
    execute,
    literal_present,
    order_paired_jobs,
    reference_probes,
    save_progress,
)
from perla_extract.study_extraction.models import EvidenceBlock
from perla_extract.study_extraction.source import _parser_implementation_sha256, _sha256


def test_generated_text_at_the_output_limit_is_not_complete():
    page = {
        "raw_output": "<doctag>incomplete",
        "tokens": 8192,
        "stop_reason": "unspecified",
    }
    assert not conversion_complete("success", [page], 1, requires_vlm=True)
    page.update(tokens=100, raw_output="<doctag>text</doctag>")
    assert conversion_complete("success", [page], 1, requires_vlm=True)
    assert not conversion_complete("success", [page], 2, requires_vlm=True)
    page.update(raw_output=None, tokens=None, stop_reason=None)
    assert not conversion_complete("success", [page], 1, requires_vlm=True)
    assert conversion_complete("success", [page], 1, requires_vlm=False)


@pytest.mark.parametrize("wrong", ["18.4", "8.40", "-8.4", "−8.4", "8.41"])
def test_probe_does_not_credit_a_different_number(wrong):
    assert not literal_present("8.4", wrong)
    assert literal_present("8.4", "PCE = 8.4 %")


def test_probes_preserve_unit_case_and_use_existing_source_citations():
    assert not literal_present("mM", "mm")
    assert literal_present("mA/cm 2", "mA/cm\n2")
    assert literal_present("%", "20%")
    assert literal_present("mW/cm2", "100mW/cm2")
    value = {
        "raw_value": "8.4",
        "unit": "%",
        "evidence": [
            {"block_id": "main_p4_0070_table_example", "quote": "A | 8.4 %"},
            {"block_id": "main_p4_0070_table_example", "quote": "A | 8.4 %"},
        ],
    }
    probes = reference_probes(value)
    assert len(probes) == 2
    assert all(p["source"] == "main" and p["page"] == 4 for p in probes)
    assert reference_probes({"raw_value": "9.4", "evidence": value["evidence"]}) == []


def test_audit_cannot_credit_a_value_on_another_page(tmp_path):
    import pymupdf

    path = tmp_path / "source.pdf"
    with pymupdf.open() as pdf:
        for text in ("8.4 %", "18.4 %"):
            page = pdf.new_page()
            page.insert_text((50, 50), text)
        pdf.save(path)
    blocks = [
        EvidenceBlock(block_id="b", source="main", page=2, kind="text", text="8.4 %")
    ]
    metrics, probes = audit(
        blocks,
        {
            "pdf": str(path),
            "source": "main",
            "page_range": [1, 2],
            "probes": [
                {"page": 1, "source": "main", "literal": "8.4", "path": "/metric"}
            ],
        },
    )
    assert metrics["literal_probes_found"] == 0
    assert metrics["native_visible_probes_lost"] == 1
    assert probes[0]["native_found"] is True and probes[0]["found"] is False
    with pytest.raises(ValueError, match="outside"):
        audit(
            blocks,
            {"pdf": str(path), "source": "main", "page_range": [1, 1], "probes": []},
        )


def test_timeout_is_reported_and_finished_attempt_is_not_retried(tmp_path, monkeypatch):
    calls = []

    def timeout(*args, **kwargs):
        calls.append((args, kwargs["timeout"]))
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", timeout)
    plan = {
        "jobs": [
            {
                "paper_id": "paper",
                "source": "main",
                "condition": "granite",
                "timeout_seconds": 1980,
            }
        ],
        "timeout_seconds": 1,
    }
    from pathlib import Path

    from examples.parsing import compare

    plan.update(
        experiment_sha256=_sha256(Path(compare.__file__)),
        adapter_sha256=_parser_implementation_sha256(),
    )
    execute(plan, tmp_path)
    execute(plan, tmp_path)
    assert len(calls) == 1
    assert calls[0][1] == 1980
    metrics = json.loads((tmp_path / "paper/main/granite/metrics.json").read_text())
    assert metrics["status"] == "timeout"
    assert "timeout" in (tmp_path / "results.csv").read_text()
    assert json.loads((tmp_path / "summary.json").read_text())["complete"] == 0


def test_order_keeps_full_paper_comparisons_together_without_dropping_conditions():
    cases = []
    for paper, source, pages in [
        ("long", "main", 10),
        ("short", "supplement", 2),
        ("short", "main", 3),
    ]:
        for condition in ["standard", "granite", "granite_native"]:
            cases.append(
                {
                    "paper_id": paper,
                    "source": source,
                    "condition": condition,
                    "page_range": [1, pages],
                }
            )
    ordered = order_paired_jobs(cases)
    assert ordered == order_paired_jobs(cases)
    assert [(x["paper_id"], x["source"]) for x in ordered] == [
        ("short", "main")
    ] * 3 + [("short", "supplement")] * 3 + [("long", "main")] * 3
    for start in range(0, len(ordered), 3):
        assert {x["condition"] for x in ordered[start : start + 3]} == {
            "standard",
            "granite",
            "granite_native",
        }


def test_partial_batch_results_are_published_before_the_whole_study_finishes(tmp_path):
    rows = [
        {
            "paper_id": "p",
            "source": "main",
            "condition": "standard",
            "status": "complete",
        }
    ]
    save_progress(rows, tmp_path, planned=3)
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert (
        summary["status"] == "running"
        and summary["attempted"] == 1
        and summary["planned"] == 3
    )
    assert "standard" in (tmp_path / "results.csv").read_text()
    assert not (tmp_path / "results.csv.tmp").exists()
