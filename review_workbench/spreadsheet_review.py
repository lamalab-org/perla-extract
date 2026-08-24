"""Create and read the deliberately small offline-review workbook.

The workbook is an alternate editor for scalar values, not a second scientific
schema. Stable record identities, paths, and the source revision remain read-only;
the importer compares them with a freshly generated contract before returning any
changes. This keeps Excel convenient without allowing row edits to change record
structure or bypass whole-study validation in :mod:`review_workbench.study_review`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from typing import Any, Iterable
from zipfile import BadZipFile, ZipFile

from openpyxl import Workbook, load_workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.table import Table, TableStyleInfo

FORMAT_NAME = "perla-review-workbook"
FORMAT_VERSION = 2
FIXED_SHEETS = ("Instructions", "Record review")
RECORD_HEADERS = (
    "Record type",
    "record_type",
    "record_id",
    "family context",
    "device context",
    "Label",
    "Review outcome",
    "Reviewer note",
)
FIELD_HEADERS = (
    "Record type",
    "record_type",
    "record_id",
    "Field group",
    "Field",
    "JSON path",
    "Current value",
    "Reviewed value",
    "Value type",
    "Editable",
    "Reviewer note",
    "Evidence block",
    "Evidence quote",
)
OUTCOME_TO_DECISION = {
    "All fields match source": "verified",
    "Cannot establish from source": "uncertain",
    "Correct fields": "needs_correction",
}
DECISION_TO_OUTCOME = {value: key for key, value in OUTCOME_TO_DECISION.items()}
NO_OUTCOME = "Not reviewed"
MAX_WORKBOOK_BYTES = 15 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024


@dataclass(frozen=True)
class WorkbookChange:
    """One scalar correction recovered from a validated workbook row."""

    collection: str
    record_id: str
    path: str
    value: Any
    note: str
    evidence: tuple[dict[str, str], ...]


@dataclass(frozen=True)
class WorkbookDecision:
    """One complete-record judgment recovered from the record-review sheet."""

    collection: str
    record_id: str
    decision: str
    note: str


@dataclass(frozen=True)
class WorkbookReview:
    """Validated edits and metadata ready for one atomic review transition."""

    base_revision: int
    scope_device_id: str | None
    changes: tuple[WorkbookChange, ...]
    decisions: tuple[WorkbookDecision, ...]
    sha256: str


@dataclass(frozen=True)
class _FieldRow:
    values: tuple[Any, ...]
    current_value: Any
    editable: bool


def _pointer_part(value: object) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def _value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return "text"


def _citation(value: Any, inherited: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return inherited
    evidence = value.get("evidence")
    return evidence[0] if isinstance(evidence, list) and evidence else inherited


def _scalar_rows(
    collection: str,
    record_id: str,
    record_index: int,
    value: Any,
    label: str,
    path: tuple[str | int, ...] = (),
    inherited_citation: dict[str, Any] | None = None,
) -> Iterable[_FieldRow]:
    """Flatten schema leaves while retaining paths and their nearest evidence.

    Excel cells should contain one atomic value. Recursion is generic over nested
    Pydantic output, while ``evidence`` is represented in dedicated support columns
    instead of being exposed as editable scientific data.
    """

    citation = _citation(value, inherited_citation)
    if isinstance(value, list):
        for index, item in enumerate(value):
            yield from _scalar_rows(
                collection,
                record_id,
                record_index,
                item,
                label,
                (*path, index),
                citation,
            )
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if key != "evidence":
                yield from _scalar_rows(
                    collection,
                    record_id,
                    record_index,
                    item,
                    label,
                    (*path, key),
                    citation,
                )
        return

    field = str(path[-1])
    editable = not field.endswith("_id")
    json_path = "/" + "/".join(
        _pointer_part(part) for part in (collection, record_index, *path)
    )
    yield _FieldRow(
        values=(
            label,
            collection,
            record_id,
            " › ".join(str(part) for part in path[:-1]),
            field,
            json_path,
            value,
            value,
            _value_type(value),
            "Yes" if editable else "No",
            "",
            str(citation.get("block_id", "")) if citation else "",
            str(citation.get("quote", "")) if citation else "",
        ),
        current_value=value,
        editable=editable,
    )


def _records_in_scope(
    truth: dict[str, Any],
    identifiers: dict[str, str],
    device_id: str | None,
) -> list[tuple[str, int, dict[str, Any]]]:
    """Select either the paper or one device plus the context needed to review it."""

    if device_id is None:
        return [
            (collection, index, record)
            for collection in identifiers
            for index, record in enumerate(truth.get(collection, []))
        ]
    devices = truth.get("individual_devices", [])
    device = next(
        (
            item
            for item in devices
            if str(item.get(identifiers["individual_devices"])) == device_id
        ),
        None,
    )
    if device is None:
        raise ValueError(f"unknown device {device_id}")
    family_id = device.get(identifiers["device_families"])
    selected: list[tuple[str, int, dict[str, Any]]] = []
    for collection in identifiers:
        for index, record in enumerate(truth.get(collection, [])):
            include = False
            if collection == "device_families":
                include = record.get(identifiers[collection]) == family_id
            elif collection == "individual_devices":
                include = record.get(identifiers[collection]) == device_id
            elif collection in {"performance_observations", "stability_tests"}:
                linked_device = record.get(identifiers["individual_devices"])
                linked_family = record.get(identifiers["device_families"])
                include = linked_device == device_id or (
                    not linked_device and linked_family == family_id
                )
            elif collection == "population_statistics":
                include = record.get(identifiers["device_families"]) == family_id
            if include:
                selected.append((collection, index, record))
    return selected


def _contract(
    truth: dict[str, Any],
    identifiers: dict[str, str],
    labels: dict[str, str],
    device_id: str | None,
) -> tuple[list[tuple[Any, ...]], list[_FieldRow]]:
    records, fields = [], []
    for collection, index, record in _records_in_scope(
        truth, identifiers, device_id
    ):
        record_id = str(record[identifiers[collection]])
        linked_family = str(record.get(identifiers["device_families"], "") or "")
        linked_device = str(record.get(identifiers["individual_devices"], "") or "")
        records.append(
            (
                labels[collection],
                collection,
                record_id,
                linked_family,
                linked_device,
                str(record.get("label") or record.get("specimen_label") or ""),
                NO_OUTCOME,
                "",
            )
        )
        fields.extend(
            _scalar_rows(collection, record_id, index, record, labels[collection])
        )
    return records, fields


def _style_sheet(sheet: Any, widths: tuple[int, ...]) -> None:
    sheet.sheet_view.showGridLines = False
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for index, width in enumerate(widths, 1):
        sheet.column_dimensions[chr(64 + index)].width = width
    for cell in sheet[1]:
        cell.fill = PatternFill("solid", fgColor="176B52")
        cell.font = Font(bold=True, color="FFFFFF")
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")


def _field_sheet_name(collection: str) -> str:
    """Turn a schema collection name into a stable, readable Excel tab name.

    Collection keys are the durable grouping already defined by the scientific
    schema. Reusing them avoids maintaining a second list of domain categories in
    the spreadsheet layer. Excel limits worksheet titles to 31 characters.
    """

    return collection.replace("_", " ").title()[:31]


def _field_rows_by_collection(
    field_rows: Iterable[_FieldRow], identifiers: dict[str, str]
) -> dict[str, list[_FieldRow]]:
    """Group flattened scalar rows by their existing top-level record collection."""

    grouped: dict[str, list[_FieldRow]] = {collection: [] for collection in identifiers}
    for row in field_rows:
        grouped[str(row.values[1])].append(row)
    return {collection: rows for collection, rows in grouped.items() if rows}


def _add_field_sheet(
    book: Workbook, collection: str, field_rows: list[_FieldRow]
) -> None:
    """Add one filterable correction tab for a scientific record collection."""

    sheet = book.create_sheet(_field_sheet_name(collection))
    sheet.append(FIELD_HEADERS)
    for field_row in field_rows:
        sheet.append(field_row.values)
    _keep_source_strings_literal(sheet)
    _style_sheet(sheet, (19, 27, 32, 34, 23, 48, 28, 28, 13, 11, 38, 28, 68))
    sheet.freeze_panes = "D2"
    sheet.add_table(
        Table(
            displayName=f"Fields_{collection}",
            ref=f"A1:M{len(field_rows) + 1}",
            tableStyleInfo=TableStyleInfo(
                name="TableStyleMedium2",
                showRowStripes=True,
                showColumnStripes=False,
            ),
        )
    )
    type_validation = DataValidation(
        type="list", formula1='"text,integer,number,boolean,null"'
    )
    sheet.add_data_validation(type_validation)
    type_validation.add(f"I2:I{len(field_rows) + 1}")
    for row_index, field_row in enumerate(field_rows, 2):
        fill = "FFF3BF" if field_row.editable else "E7E9E8"
        for column in (8, 9, 11, 12, 13):
            sheet.cell(row_index, column).fill = PatternFill("solid", fgColor=fill)
    sheet.conditional_formatting.add(
        f"A2:M{len(field_rows) + 1}",
        FormulaRule(
            formula=["$G2<>$H2"], fill=PatternFill("solid", fgColor="FFE0B2")
        ),
    )


def _keep_source_strings_literal(sheet: Any) -> None:
    """Prevent source text beginning with ``=`` from becoming an Excel formula."""

    for row in sheet.iter_rows():
        for cell in row:
            if isinstance(cell.value, str) and cell.value.startswith("="):
                cell.data_type = "s"


def create_review_workbook(
    *,
    truth: dict[str, Any],
    identifiers: dict[str, str],
    labels: dict[str, str],
    paper_id: str,
    split: str,
    revision: int,
    schema_sha256: str,
    current_decisions: dict[str, str] | None = None,
    device_id: str | None = None,
) -> bytes:
    """Return an XLSX review form bound to one immutable study revision."""

    record_rows, field_rows = _contract(truth, identifiers, labels, device_id)
    if not record_rows:
        raise ValueError(
            "this paper has no records to review in Excel; add missing records in the browser first"
        )
    current_decisions = current_decisions or {}
    book = Workbook()
    instructions = book.active
    instructions.title = "Instructions"
    records = book.create_sheet("Record review")

    instructions.sheet_view.showGridLines = False
    instructions.merge_cells("A1:B1")
    instructions["A1"] = "PERLA offline review workbook"
    instructions["A1"].fill = PatternFill("solid", fgColor="176B52")
    instructions["A1"].font = Font(bold=True, color="FFFFFF", size=16)
    guidance = (
        ("Paper", truth.get("paper", {}).get("title") or paper_id),
        ("Scope", f"Device {device_id} plus linked context" if device_id else "All paper records"),
        ("1", "Give one outcome for each complete record on Record review."),
        ("2", "Correct wrong scalar values on the record-type tabs that follow. Edit yellow cells; identifiers are read-only."),
        ("3", "Keep atomic values separate. Do not combine multiple measurements, conditions, or outcomes in one cell."),
        ("4", "Every correction needs a short note and an exact evidence block and quote."),
        ("5", "You may sort or filter rows. Do not add, delete, or rename rows or sheets; add or remove complete records in the browser."),
        ("6", "Upload this file to the same paper. Stale or structurally changed workbooks are rejected."),
    )
    for row in guidance:
        instructions.append(row)
    instructions.column_dimensions["A"].width = 18
    instructions.column_dimensions["B"].width = 90
    for row in instructions.iter_rows(min_row=3):
        row[0].fill = PatternFill("solid", fgColor="E2F2EC")
        row[0].font = Font(bold=True, color="176B52")
        row[1].alignment = Alignment(wrap_text=True, vertical="top")

    records.append(RECORD_HEADERS)
    for row in record_rows:
        key = f"{row[1]}:{row[2]}"
        outcome = DECISION_TO_OUTCOME.get(current_decisions.get(key, ""), NO_OUTCOME)
        records.append((*row[:6], outcome, row[7]))
    _keep_source_strings_literal(records)
    _style_sheet(records, (19, 28, 32, 30, 30, 54, 28, 45))
    records.add_table(
        Table(
            displayName="RecordReviewTable",
            ref=f"A1:H{len(record_rows) + 1}",
            tableStyleInfo=TableStyleInfo(
                name="TableStyleMedium2", showRowStripes=True, showColumnStripes=False
            ),
        )
    )
    outcome_validation = DataValidation(
        type="list",
        formula1='"Not reviewed,All fields match source,Cannot establish from source,Correct fields"',
    )
    records.add_data_validation(outcome_validation)
    outcome_validation.add(f"G2:G{len(record_rows) + 1}")
    for row in records.iter_rows(min_row=2, min_col=7, max_col=8):
        for cell in row:
            cell.fill = PatternFill("solid", fgColor="FFF3BF")

    grouped_fields = _field_rows_by_collection(field_rows, identifiers)
    for collection, collection_rows in grouped_fields.items():
        _add_field_sheet(book, collection, collection_rows)

    meta = book.create_sheet("_meta")

    metadata = {
        "format": FORMAT_NAME,
        "format_version": FORMAT_VERSION,
        "paper_id": paper_id,
        "split": split,
        "base_revision": revision,
        "scope_device_id": device_id or "",
        "schema_sha256": schema_sha256,
        "ground_truth_sha256": _json_digest(truth),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "record_count": len(record_rows),
        "field_count": len(field_rows),
    }
    meta.append(("key", "value"))
    for key, value in metadata.items():
        meta.append((key, value))
    _style_sheet(meta, (34, 78))
    meta.sheet_state = "hidden"

    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _json_digest(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _cell_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _decode_value(value: Any, kind: str) -> Any:
    if kind == "null":
        if value not in (None, ""):
            raise ValueError("a null value must have an empty reviewed-value cell")
        return None
    if kind == "text":
        return "" if value is None else str(value)
    if kind == "boolean":
        if isinstance(value, bool):
            return value
        normalized = _cell_text(value).lower()
        if normalized in {"true", "yes", "1"}:
            return True
        if normalized in {"false", "no", "0"}:
            return False
        raise ValueError(f"{value!r} is not a boolean")
    if kind == "integer":
        number = float(value)
        if not number.is_integer():
            raise ValueError(f"{value!r} is not an integer")
        return int(number)
    if kind == "number":
        return float(value)
    raise ValueError(f"unknown value type {kind!r}")


def _metadata(sheet: Any) -> dict[str, Any]:
    return {
        str(key): value
        for key, value in sheet.iter_rows(min_row=2, max_col=2, values_only=True)
        if key is not None
    }


def _rows(sheet: Any, headers: tuple[str, ...]) -> list[tuple[Any, ...]]:
    if any(cell.data_type == "f" for row in sheet.iter_rows() for cell in row):
        raise ValueError(f"{sheet.title} contains a formula; enter literal values only")
    actual_headers = tuple(cell.value for cell in sheet[1])
    if actual_headers != headers:
        raise ValueError(f"{sheet.title} columns were changed")
    return [
        tuple(cell.value for cell in row)
        for row in sheet.iter_rows(min_row=2, max_col=len(headers))
        if any(cell.value is not None for cell in row)
    ]


def read_review_workbook(
    data: bytes,
    *,
    truth: dict[str, Any],
    identifiers: dict[str, str],
    labels: dict[str, str],
    paper_id: str,
    split: str,
    revision: int,
    schema_sha256: str,
) -> WorkbookReview:
    """Validate an uploaded workbook and return only its intentional edits."""

    if not data or len(data) > MAX_WORKBOOK_BYTES:
        raise ValueError("review workbook must be a non-empty XLSX smaller than 15 MiB")
    try:
        with ZipFile(BytesIO(data)) as archive:
            if sum(item.file_size for item in archive.infolist()) > MAX_UNCOMPRESSED_BYTES:
                raise ValueError("review workbook expands beyond the 100 MiB safety limit")
    except BadZipFile as error:
        raise ValueError("review workbook is not a readable XLSX file") from error
    try:
        book = load_workbook(
            BytesIO(data), data_only=False, read_only=False, keep_links=False
        )
    except Exception as error:  # malformed OOXML can fail in several XML readers
        raise ValueError("review workbook is not a readable XLSX file") from error
    if "_meta" not in book.sheetnames:
        raise ValueError("review workbook metadata sheet is missing")
    metadata = _metadata(book["_meta"])
    expected_meta = {
        "format": FORMAT_NAME,
        "format_version": FORMAT_VERSION,
        "paper_id": paper_id,
        "split": split,
        "base_revision": revision,
        "schema_sha256": schema_sha256,
        "ground_truth_sha256": _json_digest(truth),
    }
    for key, expected in expected_meta.items():
        if metadata.get(key) != expected:
            if key in {"base_revision", "ground_truth_sha256"}:
                raise ValueError(
                    "this workbook was downloaded from an older paper revision; "
                    "download a new workbook and transfer the reviewed changes"
                )
            if key == "format_version":
                raise ValueError(
                    "this workbook uses an older layout; download a fresh workbook"
                )
            raise ValueError(f"review workbook metadata does not match {key}")
    device_id = _cell_text(metadata.get("scope_device_id")) or None
    expected_records, expected_fields = _contract(
        truth, identifiers, labels, device_id
    )
    grouped_fields = _field_rows_by_collection(expected_fields, identifiers)
    expected_sheets = (
        *FIXED_SHEETS,
        *(_field_sheet_name(collection) for collection in grouped_fields),
        "_meta",
    )
    if tuple(book.sheetnames) != expected_sheets:
        raise ValueError("review workbook sheets were added, removed, or renamed")
    record_rows = _rows(book["Record review"], RECORD_HEADERS)
    field_rows = [
        (sheet_name, row_number, row)
        for collection in grouped_fields
        for sheet_name in (_field_sheet_name(collection),)
        for row_number, row in enumerate(
            _rows(book[sheet_name], FIELD_HEADERS), start=2
        )
    ]
    if len(record_rows) != len(expected_records) or len(field_rows) != len(expected_fields):
        raise ValueError("review workbook rows were added, removed, or left incomplete")

    expected_record_map = {(row[1], row[2]): row for row in expected_records}
    actual_record_keys = [(_cell_text(row[1]), _cell_text(row[2])) for row in record_rows]
    if len(set(actual_record_keys)) != len(actual_record_keys) or set(
        actual_record_keys
    ) != set(expected_record_map):
        raise ValueError("Record review identities were added, removed, or duplicated")
    decisions: list[WorkbookDecision] = []
    for row_number, actual in enumerate(record_rows, 2):
        expected = expected_record_map[(_cell_text(actual[1]), _cell_text(actual[2]))]
        if tuple(map(_cell_text, actual[:6])) != tuple(map(_cell_text, expected[:6])):
            raise ValueError(f"Record review row {row_number} identity was changed")
        outcome = _cell_text(actual[6]) or NO_OUTCOME
        if outcome not in {NO_OUTCOME, *OUTCOME_TO_DECISION}:
            raise ValueError(f"Record review row {row_number} has an unknown outcome")
        if outcome != NO_OUTCOME:
            decisions.append(
                WorkbookDecision(
                    collection=str(actual[1]),
                    record_id=str(actual[2]),
                    decision=OUTCOME_TO_DECISION[outcome],
                    note=_cell_text(actual[7]),
                )
            )

    expected_field_map = {str(row.values[5]): row for row in expected_fields}
    actual_field_keys = [_cell_text(row[5]) for _, _, row in field_rows]
    if len(set(actual_field_keys)) != len(actual_field_keys) or set(
        actual_field_keys
    ) != set(expected_field_map):
        raise ValueError("Field correction paths were added, removed, or duplicated")
    changes: list[WorkbookChange] = []
    for sheet_name, row_number, actual in field_rows:
        expected = expected_field_map[_cell_text(actual[5])]
        expected_values = expected.values
        immutable_text_columns = (0, 1, 2, 3, 4, 5, 9)
        if any(
            _cell_text(actual[index]) != _cell_text(expected_values[index])
            for index in immutable_text_columns
        ):
            raise ValueError(f"{sheet_name} row {row_number} identity was changed")
        try:
            current = _decode_value(actual[6], _value_type(expected.current_value))
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"{sheet_name} row {row_number} current value was changed"
            ) from error
        if current != expected.current_value:
            raise ValueError(f"{sheet_name} row {row_number} current value was changed")
        reviewed_type = _cell_text(actual[8])
        try:
            reviewed = _decode_value(actual[7], reviewed_type)
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"{sheet_name} row {row_number} has an invalid reviewed value: {error}"
            ) from error
        if reviewed == expected.current_value and reviewed_type == _value_type(
            expected.current_value
        ):
            continue
        if not expected.editable:
            raise ValueError(f"{sheet_name} row {row_number} is read-only")
        note = _cell_text(actual[10])
        block_id, quote = _cell_text(actual[11]), _cell_text(actual[12])
        if not note:
            raise ValueError(f"{sheet_name} row {row_number} needs a reviewer note")
        if not block_id or not quote:
            raise ValueError(f"{sheet_name} row {row_number} needs exact evidence")
        changes.append(
            WorkbookChange(
                collection=str(actual[1]),
                record_id=str(actual[2]),
                path=str(actual[5]),
                value=reviewed,
                note=note,
                evidence=({"block_id": block_id, "quote": quote},),
            )
        )
    if not changes and not decisions:
        raise ValueError("review workbook contains no decisions or corrections")
    return WorkbookReview(
        base_revision=revision,
        scope_device_id=device_id,
        changes=tuple(changes),
        decisions=tuple(decisions),
        sha256=hashlib.sha256(data).hexdigest(),
    )
