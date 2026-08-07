#!/usr/bin/env python3
"""Seed idempotent, main-paper-supported review findings into private Blob state."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from review_workbench.api.index import BlobStore, VercelReviewApplication
from review_workbench.review_collaboration import add_user
from review_workbench.server import ReviewApplication


FINDINGS = (
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
            if any(
                issue.get("description") == finding["description"]
                for issue in existing
            ):
                skipped += 1
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
