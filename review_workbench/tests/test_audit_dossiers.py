import json

import fitz

from review_workbench.build_audit_dossiers import build_dossier


def test_build_dossier_links_fields_and_groups_unmapped_candidates(tmp_path):
    pdf_path = tmp_path / "paper.pdf"
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), "Champion device PCE 20.1% and Voc 1.12 V. Stability retained 90% after 100 h.")
        document.save(pdf_path)
    truth_path = tmp_path / "paper.json"
    truth_path.write_text(json.dumps({"cells": [{"pce": {"value": 20.1, "unit": "%"}, "layers": []}]}))

    dossier = build_dossier(pdf_path, truth_path)

    assert dossier["cells"][0]["performance"]["pce"]["value"] == 20.1
    assert any(item["path"] == "/cells/0/pce/value" for item in dossier["fact_evidence"])
    assert any(group["category"] == "stability" for group in dossier["unmapped_candidates"])
