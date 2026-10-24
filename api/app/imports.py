"""Immutable owned import previews. This module never inserts employees."""

from collections import defaultdict
from dataclasses import asdict
from decimal import Decimal, DecimalException
import hashlib
import json
from pathlib import Path
import secrets
import time

from app.database import read_transaction, transaction
from app.models import COUNTRY_CURRENCIES, Employee, validate_employee_field
from app.money import format_money, parse_money
from app.normalization import normalized_key, required_text
from app.xlsx import HEADERS, ImportProblem, choose_sheet, header, inspection, issue, read_workbook

FIELDS = tuple(HEADERS.values())
LABELS = {field: label for label, field in HEADERS.items()}


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def database_snapshot(connection):
    return {
        "employees": [dict(row) for row in connection.execute(
            "SELECT e.employee_id_key,e.email_key,e.revision,i.generation FROM employees e "
            "JOIN employee_identities i USING(employee_id) ORDER BY e.employee_id_key")],
        "bands": [dict(row) for row in connection.execute(
            "SELECT b.*,i.generation FROM salary_bands b JOIN band_identities i USING(country,level) "
            "ORDER BY country,level")],
    }


def inspect(contents):
    return {"source_digest": hashlib.sha256(contents).hexdigest(), **inspection(read_workbook(contents))}


def draft_rows(sheet):
    _, mapping, file_errors = header(sheet)
    if file_errors:
        return [], file_errors
    rows, occurrences = [], {"employee_id": defaultdict(list), "email": defaultdict(list)}
    for number, cells in sheet.rows[1:]:
        values, errors, salary_input, rounded, identity_keys = {}, [], None, None, {}
        for column, field in mapping.items():
            cell = cells.get(column)
            value = cell.value if cell else ""
            if field in occurrences and cell and cell.formula is None and cell.kind in {"s", "inlineStr"}:
                try:
                    identity_keys[field] = normalized_key(required_text(value, field))
                    occurrences[field][identity_keys[field]].append(number)
                except ValueError:
                    pass
            try:
                if cell and cell.formula is not None:
                    raise ValueError("Formulas are not allowed")
                if not cell or not value.strip():
                    raise ValueError("Required value is missing")
                if field == "salary_minor":
                    salary_input = value
                    if cell.kind not in {"n", "s", "inlineStr"}:
                        raise ValueError("Expected decimal text or a numeric salary cell")
                    minor = parse_money(Decimal(value) if cell.kind == "n" else value)
                    values[field] = validate_employee_field(field, minor)
                    rounded = Decimal(value) != Decimal(format_money(minor))
                else:
                    if cell.kind not in {"s", "inlineStr"}:
                        raise ValueError("Expected text; store identifiers as Excel text to preserve leading zeros")
                    values[field] = validate_employee_field(field, value)
            except (ValueError, DecimalException) as error:
                errors.append(issue(sheet.name, number, LABELS[field], "formula" if cell and cell.formula is not None else "invalid_field", str(error)))
        for column, cell in cells.items():
            if column not in mapping and (cell.value.strip() or cell.formula is not None):
                errors.append(issue(sheet.name, number, column, "formula" if cell.formula is not None else "unexpected_column",
                                    "Formulas are not allowed" if cell.formula is not None else "Data exists outside the approved columns"))
        if not errors:
            # Final shared record invariant check, after collecting every field error.
            Employee(**values)
        rows.append({"row_number": number, "values": values, "errors": errors, "salary_input": salary_input,
                     "rounding_applied": rounded, "identity_keys": identity_keys, "raw_cells": {column: asdict(cell) for column, cell in cells.items()}})
    by_number = {row["row_number"]: row for row in rows}
    for field, keys in occurrences.items():
        for numbers in keys.values():
            if len(numbers) > 1:
                for number in numbers:
                    by_number[number]["errors"].append(issue(sheet.name, number, LABELS[field], "duplicate_in_file",
                        "Every occurrence of this duplicate must be corrected", duplicate_count=len(numbers), first_row=numbers[0]))
    if not rows:
        file_errors.append(issue(sheet.name, None, "Rows", "no_data_rows", "Worksheet contains no employee rows"))
    return rows, file_errors


def create_preview(path: Path, owner: str, contents: bytes, worksheet: str | None = None):
    sheet = choose_sheet(read_workbook(contents), worksheet)
    rows, file_errors = draft_rows(sheet)
    with transaction(path) as connection:
        existing = connection.execute("SELECT employee_id_key,email_key FROM employees").fetchall()
        existing_keys = {"employee_id": {row["employee_id_key"] for row in existing},
                         "email": {row["email_key"] for row in existing}}
        bands = {(row["country"], row["level"]): dict(row) for row in connection.execute(
            "SELECT b.*,i.generation FROM salary_bands b JOIN band_identities i USING(country,level)")}
        for row in rows:
            values = row["values"]
            for field in ("employee_id", "email"):
                key = row["identity_keys"].get(field)
                if key and key in existing_keys[field]:
                    row["errors"].append(issue(sheet.name, row["row_number"], LABELS[field], "existing_employee",
                                               "An employee with this value already exists; nothing will be overwritten or skipped"))
            classification = values.get("country"), values.get("level")
            band = bands.get(classification)
            row["band_status"] = ("Band-matched" if band else "Unbanded") if all(classification) else None
            row["band"] = band
            row["valid"] = not row["errors"]
        errors = [*file_errors, *(error for row in rows for error in row["errors"])]
        valid = sum(row["valid"] for row in rows)
        now, preview_id = int(time.time()), secrets.token_urlsafe(32)
        proposal = {"rows": [{"row_number": row["row_number"],
                    "values": {field: row["values"].get(field) for field in FIELDS},
                    "band_status": row["band_status"], "band": row["band"], "valid": row["valid"]}
                    for row in rows], "errors": errors}
        metadata = {"preview_id": preview_id, "owner": normalized_key(owner), "worksheet": sheet.name,
                    "source_digest": hashlib.sha256(contents).hexdigest(), "proposed_digest": digest(proposal),
                    "created_at": now, "expires_at": now + 1800, "status": "invalid" if errors else "clean",
                    "total_rows": len(rows), "valid_count": valid, "invalid_count": len(rows) - valid,
                    "error_count": len(errors), "unbanded_count": sum(row["band_status"] == "Unbanded" for row in rows),
                    "valid_unbanded_count": sum(row["valid"] and row["band_status"] == "Unbanded" for row in rows)}
        columns = ",".join(metadata)
        connection.execute(f"INSERT INTO import_previews ({columns}) VALUES ({','.join('?' for _ in metadata)})", tuple(metadata.values()))
        # Capture all employee lifetimes/revisions and the full band catalog in
        # the same write snapshot as validation. Old v5 previews lack this proof.
        snapshot = database_snapshot(connection)
        connection.execute("INSERT INTO import_preview_context VALUES (?,?,?)",
                           (preview_id, canonical(snapshot), digest(snapshot)))
        for row in rows:
            band = row["band"] or {}
            connection.execute("""INSERT INTO import_preview_rows (preview_id,row_number,employee_id,name,email,department,level,country,
                salary_minor,salary_input,rounding_applied,valid,raw_cells,band_status,band_revision,band_generation,
                band_minimum_minor,band_midpoint_minor,band_maximum_minor) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (preview_id, row["row_number"], *(row["values"].get(field) for field in FIELDS), row["salary_input"],
                 None if row["rounding_applied"] is None else int(row["rounding_applied"]), int(row["valid"]), canonical(row["raw_cells"]),
                 row["band_status"], band.get("revision"), band.get("generation"), band.get("minimum_minor"), band.get("midpoint_minor"), band.get("maximum_minor")))
        for index, error in enumerate(errors, 1):
            context = {key: value for key, value in error.items() if key not in {"sheet", "row", "field", "code", "message"}}
            connection.execute("INSERT INTO import_preview_errors VALUES (?,?,?,?,?,?,?)",
                (preview_id, index, error["row"], error["field"], error["code"], error["message"], canonical(context)))
        connection.execute("UPDATE import_previews SET sealed=1 WHERE preview_id=?", (preview_id,))
        return metadata


def owned_preview(connection, owner, preview_id):
    row = connection.execute("SELECT * FROM import_previews WHERE preview_id=? AND owner=? AND sealed=1",
                             (preview_id, normalized_key(owner))).fetchone()
    if row is None:
        raise ImportProblem(404, "preview_not_found", "Preview does not exist")
    if int(time.time()) >= row["expires_at"]:
        raise ImportProblem(410, "preview_expired", "Preview expired; upload the workbook to create a new preview")
    return {key: row[key] for key in row.keys() if key != "sealed"}


def preview(path, owner, preview_id):
    with read_transaction(path) as connection:
        return owned_preview(connection, owner, preview_id)


def error_wire(row, worksheet):
    return {"sheet": worksheet, "row": row["row_number"], "field": row["field"], "code": row["code"],
            "message": row["message"], **json.loads(row["context"])}


def pages(path, owner, preview_id, kind, page=1, page_size=25):
    if kind not in {"rows", "errors"}:
        raise ValueError("Invalid preview page kind")
    if type(page) is not int or page < 1 or type(page_size) is not int or page_size not in {25, 50, 100}:
        raise ValueError("Invalid pagination")
    with read_transaction(path) as connection:
        info = owned_preview(connection, owner, preview_id)
        total = info["total_rows"] if kind == "rows" else info["error_count"]
        offset = (page - 1) * page_size
        records = [] if offset >= total else connection.execute(
            f"SELECT * FROM import_preview_{kind} WHERE preview_id=? ORDER BY {'row_number' if kind == 'rows' else 'error_index'} LIMIT ? OFFSET ?",
            (preview_id, page_size, offset)).fetchall()
        if kind == "errors":
            items = [error_wire(row, info["worksheet"]) for row in records]
        else:
            errors = defaultdict(list)
            if records:
                for error in connection.execute(f"SELECT * FROM import_preview_errors WHERE preview_id=? AND row_number IN ({','.join('?' for _ in records)}) ORDER BY error_index",
                    (preview_id, *(row["row_number"] for row in records))):
                    errors[error["row_number"]].append(error_wire(error, info["worksheet"]))
            items = []
            for row in records:
                values = {field: row[field] for field in FIELDS}
                if values["salary_minor"] is not None:
                    values["salary_minor"] = str(values["salary_minor"])
                band = None if row["band_revision"] is None else {"revision": row["band_revision"],
                    "etag": f'"{row["band_generation"]}:{row["band_revision"]}"',
                    **{field + "_minor": str(row["band_" + field + "_minor"]) for field in ("minimum", "midpoint", "maximum")}}
                items.append({"sheet": info["worksheet"], "row_number": row["row_number"], "valid": bool(row["valid"]),
                    "values": values, "salary_amount": format_money(row["salary_minor"]) if row["salary_minor"] is not None else None,
                    "salary_input": row["salary_input"], "rounding_applied": bool(row["rounding_applied"]) if row["rounding_applied"] is not None else None,
                    "currency": COUNTRY_CURRENCIES.get(row["country"]), "band_status": row["band_status"], "band": band,
                    "raw_cells": json.loads(row["raw_cells"]), "errors": errors[row["row_number"]]})
    return {"preview_id": preview_id, "status": info["status"], "items": items, "total": total,
            "page": page, "page_size": page_size, "total_pages": (total + page_size - 1) // page_size}
