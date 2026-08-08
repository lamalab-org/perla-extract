#!/usr/bin/env python3
"""Seed idempotent, main-paper-supported review findings into private Blob state."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from review_workbench.api.index import BlobStore, VercelReviewApplication
from review_workbench.review_collaboration import add_user, save_issues
from review_workbench.server import ReviewApplication


def _mfcl_passivation_patch() -> list[dict]:
    patch = []
    for cell_index in (0, 1):
        for layer_index, location, solvent, concentration in (
            (1, "Buried surface passivation (BSP)", "DMF", 0.50),
            (2, "Top surface passivation (TSP)", "isopropanol", 0.75),
        ):
            path = f"/cells/{cell_index}/layers/{layer_index}/deposition/-"
            patch.extend(
                (
                    {
                        "op": "add",
                        "path": path,
                        "value": {
                            "step_name": location,
                            "method": "Spin-coating",
                            "duration": {"value": 30.0, "unit": "s"},
                            "solution": {
                                "solutes": [
                                    {
                                        "name": "MFCl",
                                        "concentration": {
                                            "value": concentration,
                                            "unit": "mg/mL",
                                        },
                                    }
                                ],
                                "solvents": [
                                    {"name": solvent, "volume_fraction": 1.0}
                                ],
                            },
                            "additional_parameters": {"speed": "5000 rpm"},
                        },
                    },
                    {
                        "op": "add",
                        "path": path,
                        "value": {
                            "step_name": f"Annealing after {location}",
                            "method": "Thermal-annealing",
                            "temperature": {"value": 100.0, "unit": "°C"},
                            "duration": {"value": 300.0, "unit": "s"},
                        },
                    },
                )
            )
    return patch


FINDINGS = (
    {
        "split": "test",
        "paper_id": "10.1038--s41560-022-01102-w",
        "type": "schema_limitation",
        "cell_index": 0,
        "field_path": "/cells/0/stability",
        "description": "The single-junction NBG stability test is eligible main-text evidence, but the schema cannot encode 93.7% relative PCE retention, the 30-65% RH range, or the approximately 250 h / approximately 30 °C qualifiers without coercion.",
        "suggested_value": "Record encapsulated=true, 250 h duration, 1 sun, approximately 30 °C, constant-resistance operation, and preserve 93.7% retention plus 30-65% RH in notes until qualified/range fields exist.",
        "source_page": 3,
        "source_text": "The photo stability of encapsulated NBG perovskite solar cells was measured in air with a relative humidity (RH) of 30–65%",
        "value_relation": "approximately",
        "measurement_context": "other",
        "uncertainty": "Retained 93.7% after ~250 h; RH 30-65%; temperature ~30 °C.",
    },
    {
        "split": "test",
        "paper_id": "10.1002--adfm.202212698",
        "type": "mixed_device",
        "cell_index": 0,
        "description": "Cell 1 is labelled as one champion device, but its tuple mixes differently qualified evidence: PCE 22.04% and Voc 1.134 V are in the champion discussion, Jsc is stated only as above 24.11 mA cm-2, and FF 80.47% belongs to a later MFCl-versus-PEACl comparison device (PCE 21.95%, Voc 1.14 V).",
        "suggested_value": "Keep only a coherent, explicitly supported tuple; review Jsc as a lower bound and remove or reassign FF 80.47%.",
        "source_page": 3,
        "source_text": "The champion f-PSCs can achieve JSC values above 24.11 mA cm−2",
        "aggregation": "champion",
        "value_relation": "lower_bound",
        "proposal_confidence": "high",
        "proposed_patch": [
            {"op": "test", "path": "/cells/0/ff/value", "value": 80.47},
            {"op": "remove", "path": "/cells/0/ff"},
        ],
    },
    {
        "split": "test",
        "paper_id": "10.1002--adfm.202212698",
        "type": "missing_cell",
        "cell_index": None,
        "description": "Main-text prose reports two additional coherent comparison devices that are absent from the JSON: an MFCl-passivated flexible device (FF 80.47%, Voc 1.14 V, PCE 21.95%) and a PEACl-passivated flexible device (FF 77.98%, Voc 1.13 V, PCE 21.05%).",
        "suggested_value": "Candidate A: MFCl, PCE 21.95%, Voc 1.14 V, FF 80.47%. Candidate B: PEACl, PCE 21.05%, Voc 1.13 V, FF 77.98%. Jsc is not stated in the prose and should remain null.",
        "source_page": 5,
        "source_text": "MFCl passivation can lead to a higher efficiency (21.95%) than PEACl passivation (21.05%)",
        "aggregation": "single_device",
    },
    {
        "split": "test",
        "paper_id": "10.1002--adfm.202212698",
        "type": "missing_value",
        "cell_index": None,
        "description": "The treated cells identify MFCl on SnO2 and the absorber, but do not encode the explicit passivation steps. BSP is 0.50 mg mL-1 MFCl in DMF on SnO2; TSP is 0.75 mg mL-1 MFCl in isopropanol on the perovskite. Both are spin-coated at 5000 rpm for 30 s and annealed at 100 °C for 5 min.",
        "suggested_value": "Add separate BSP and TSP deposition/treatment steps to both MFCl-treated cells; do not add them to the control.",
        "source_page": 9,
        "source_text": "For BSP treatment, MFCl solution at different concentrations in DMF was added dropwise onto the SnO2 layer",
        "proposal_confidence": "high",
        "proposed_patch": _mfcl_passivation_patch(),
    },
    {
        "split": "test",
        "paper_id": "10.1002--adma.202305822",
        "type": "mixed_device",
        "cell_index": 0,
        "description": "Cell 1 combines the 21.44% champion reverse-scan PCE with averaged Voc/Jsc/FF values; these are not one coherent device record.",
        "suggested_value": "Separate champion reverse-scan PCE from averaged device-set metrics.",
        "source_page": 6,
        "source_text": "The averaged Voc and Jsc for ES-based devices were 1.160 V and 21.59 mA cm−2",
        "aggregation": "champion",
        "measurement_context": "reverse_scan",
    },
    {
        "split": "test",
        "paper_id": "10.1002--adma.202305822",
        "type": "mixed_device",
        "cell_index": 2,
        "description": "Cell 3 combines the 20.90% n-i-p champion PCE with averaged device-set metrics, producing an internally inconsistent JV tuple.",
        "suggested_value": "Separate champion scan values from averaged n-i-p metrics.",
        "source_page": 6,
        "source_text": "with averaged Voc, Jsc, FF, and PCE of ES-based devices",
        "aggregation": "champion",
        "measurement_context": "reverse_scan",
    },
    {
        "split": "test",
        "paper_id": "10.1002--solr.202100879",
        "type": "missing_composition",
        "cell_index": 0,
        "description": "The untreated control is described only as the 3D counterpart in the main paper. Its empty formula should not be guessed, and the absorber layer must not be labelled 3D Me3SPbI3 because Me3SPbI3 is the 1D material.",
        "suggested_value": "Keep the exact 3D formula unknown; rename the layer to 3D perovskite.",
        "source_page": 6,
        "source_text": "ITO/SnO2/3D or 1D/3D perovskite/Spiro-OMeTAD/Au",
        "proposal_confidence": "high",
        "proposed_patch": [
            {"op": "test", "path": "/cells/0/layers/2/name", "value": "3D Me3SPbI3"},
            {"op": "replace", "path": "/cells/0/layers/2/name", "value": "3D perovskite"},
        ],
    },
    {
        "split": "test",
        "paper_id": "10.1038--nmat4014",
        "type": "missing_cell",
        "cell_index": None,
        "description": "Main-text Table 1 contains four coherent device/scan records (without mp-TiO2 and 200-nm mp-TiO2, each forward and reverse) that are not represented by the two current JSON cells.",
        "suggested_value": "Review four Table 1 rows as candidate cells with explicit scan direction.",
        "source_page": 5,
        "source_text": "Table 1 | Photovoltaic performance of perovskite solar cells without mp-TiO2 or with 200-nm-thick mp-TiO2",
    },
    {
        "split": "dev",
        "paper_id": "10.1021--acsaem.9b01928",
        "type": "missing_value",
        "cell_index": 0,
        "field_path": "/cells/0/number_devices",
        "description": "The main text explicitly reports a batch of 20 independent devices for each complete-device set, but number_devices is absent.",
        "suggested_value": "20",
        "source_page": 8,
        "source_text": "a batch of 20 independent devices for each device set",
        "aggregation": "distribution",
        "proposal_confidence": "high",
        "proposed_patch": [
            {"op": "test", "path": "/cells/0/number_devices", "value": None},
            {"op": "replace", "path": "/cells/0/number_devices", "value": 20},
        ],
    },
    {
        "split": "dev",
        "paper_id": "10.1021--acsaem.9b01928",
        "type": "missing_value",
        "cell_index": 1,
        "field_path": "/cells/1/number_devices",
        "description": "The main text explicitly reports five independent HTM-free samples for each device set, but number_devices is absent.",
        "suggested_value": "5",
        "source_page": 14,
        "source_text": "The PV metrics of 5 independent samples for each device set",
        "aggregation": "distribution",
        "proposal_confidence": "high",
        "proposed_patch": [
            {"op": "test", "path": "/cells/1/number_devices", "value": None},
            {"op": "replace", "path": "/cells/1/number_devices", "value": 5},
        ],
    },
    {
        "split": "test",
        "paper_id": "10.1038--s41467-023-36141-8",
        "type": "schema_limitation",
        "cell_index": None,
        "description": "The main text reports maxima and bounds (Voc up to 1.29 V, FF above 80%, Jsc up to 17 mA/cm2) that the current exact-scalar schema cannot represent safely or link unambiguously to one device.",
        "suggested_value": "Represent qualifiers and device provenance instead of coercing all maxima into one exact cell.",
        "source_page": 1,
        "source_text": "VOCs up to 1.29 V, fill factors above 80% and JSCs up to 17 mA/cm2",
        "value_relation": "range",
        "uncertainty": "Mixed upper/lower bounds reported across optimized devices.",
    },
)


FIGURE_AUDITS = (
    {
        "split": "test",
        "paper_id": "10.1002--adfm.202212698",
        "total_figures": 6,
        "schema_relevant_figures": 2,
        "figure_only_schema_figures": 2,
        "unlinked_device_statistic_figures": 1,
        "notes": "Conservative suggested audit: Figures 2 and 3 contain data that map coherently to the current schema. Figure 2 has labelled forward/reverse JV tuples in inset tables (flexible PCE: 19.18/20.01/21.69/22.04%; rigid: 21.78/22.65/24.05/24.40%). Figure 3A has labelled MPP/stability values and conditions. Both include encodable values not fully repeated in prose. Figure 1 is counted separately as unlinked individual-device statistics: its PCE/Voc/Jsc/FF swarmplots do not identify the same device across panels. Figure 4 uses bending cycles, for which the schema lacks a meaningful independent variable; Figures 5-6 report measurements outside the schema. Verify before saving.",
    },
)


def seed(*, dry_run: bool = False) -> tuple[int, int]:
    if not os.environ.get("BLOB_READ_WRITE_TOKEN"):
        raise RuntimeError("BLOB_READ_WRITE_TOKEN is not configured")
    with TemporaryDirectory(prefix="perla-review-seed-") as workspace:
        app = VercelReviewApplication(BlobStore(), Path(workspace))
        users = app.users()
        reporter = next(
            (user for user in users if user.get("name") == "Initial PDF audit"),
            None,
        )
        if reporter is None:
            reporter = add_user(app.ground_truth_dir, "Initial PDF audit")
        created = skipped = 0
        for finding in FINDINGS:
            existing = app.issues(finding["split"], finding["paper_id"])
            matched = next(
                (
                    issue
                    for issue in existing
                    if issue.get("description") == finding["description"]
                ),
                None,
            )
            if matched is not None:
                upgrades = {
                    key: finding[key]
                    for key in ("proposal_confidence", "proposed_patch")
                    if key in finding and matched.get(key) != finding[key]
                }
                if not upgrades:
                    skipped += 1
                    continue
                if not dry_run:
                    matched.update(upgrades)
                    save_issues(
                        app.ground_truth_dir,
                        finding["split"],
                        finding["paper_id"],
                        existing,
                    )
                created += 1
                continue
            if dry_run:
                created += 1
                continue
            ReviewApplication.add_missing_issue(
                app,
                finding["split"],
                finding["paper_id"],
                {"reporter_id": reporter["id"], **finding},
            )
            created += 1
        for audit in FIGURE_AUDITS:
            existing = app.figure_audits(audit["split"], audit["paper_id"])
            if reporter["id"] in existing:
                skipped += 1
                continue
            if dry_run:
                created += 1
                continue
            app.save_paper_figure_audit(
                audit["split"],
                audit["paper_id"],
                {"reviewer_id": reporter["id"], **audit},
            )
            created += 1
        if not dry_run and created:
            app._sync_state()
        return created, skipped


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    created, skipped = seed(dry_run=args.dry_run)
    print(f"created={created} skipped={skipped}")


if __name__ == "__main__":
    main()
