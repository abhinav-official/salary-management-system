"""Atomic confirmation: one write transaction and one immutable completion receipt."""

from collections import defaultdict
import json
import sqlite3
import time

from app.database import read_transaction, transaction
from app.imports import FIELDS, LABELS, canonical, database_snapshot, digest
from app.models import Employee, validate_employee_field
from app.normalization import normalized_key
from app.repository import insert_employee
from app.xlsx import ImportProblem, issue


def owned(connection, owner, preview_id):
    info = connection.execute("SELECT * FROM import_previews WHERE preview_id=? AND owner=? AND sealed=1",
                              (preview_id, normalized_key(owner))).fetchone()
    if info is None:
        raise ImportProblem(404, "preview_not_found", "Preview does not exist or belongs to another HR account")
    return info


def saved_receipt(connection, preview_id):
    result = connection.execute("SELECT * FROM import_receipts WHERE preview_id=?", (preview_id,)).fetchone()
    if result is None:
        return None
    return {**dict(result), "employee_ids": json.loads(result["employee_ids"])}


def receipt(path, owner, preview_id):
    with read_transaction(path) as connection:
        owned(connection, owner, preview_id)
        result = saved_receipt(connection, preview_id)
        if result is None:
            raise ImportProblem(404, "receipt_not_found", "Preview has not been completed")
        return result


def changes(before, after, keys):
    def wire(row):
        return None if row is None else {field: str(value) if field.endswith("_minor") else value
                                         for field, value in row.items()}
    previous = {tuple(row[key] for key in keys): row for row in before}
    current = {tuple(row[key] for key in keys): row for row in after}
    return [{"key": dict(zip(keys, key)), "expected": wire(previous.get(key)), "current": wire(current.get(key))}
            for key in sorted(previous.keys() | current.keys()) if previous.get(key) != current.get(key)]


def revalidate(connection, info):
    context = connection.execute("SELECT * FROM import_preview_context WHERE preview_id=?", (info["preview_id"],)).fetchone()
    if context is None:
        raise ImportProblem(409, "preview_stale", "Preview predates confirmation support; create a new preview", retryable=False)
    snapshot = json.loads(context["snapshot"])
    if digest(snapshot) != context["digest"]:
        raise ImportProblem(409, "preview_altered", "Preview context integrity check failed; create a new preview", retryable=False)
    records = connection.execute("SELECT * FROM import_preview_rows WHERE preview_id=? ORDER BY row_number",
                                 (info["preview_id"],)).fetchall()
    errors = [dict(row) for row in connection.execute(
        "SELECT * FROM import_preview_errors WHERE preview_id=? ORDER BY error_index", (info["preview_id"],))]
    proposal = {"rows": [], "errors": []}
    for row in records:
        band = None if row["band_revision"] is None else {
            "country": row["country"], "level": row["level"], "revision": row["band_revision"], "generation": row["band_generation"],
            **{field + "_minor": row["band_" + field + "_minor"] for field in ("minimum", "midpoint", "maximum")}}
        proposal["rows"].append({"row_number": row["row_number"], "values": {field: row[field] for field in FIELDS},
                                "band_status": row["band_status"], "band": band, "valid": bool(row["valid"])})
    if errors or len(records) != info["total_rows"] or digest(proposal) != info["proposed_digest"]:
        raise ImportProblem(409, "preview_altered", "Preview values or errors changed; create a new preview", retryable=False)

    current = database_snapshot(connection)
    employee_changes = changes(snapshot["employees"], current["employees"], ("employee_id_key",))
    band_changes = changes(snapshot["bands"], current["bands"], ("country", "level"))
    bands = {(band["country"], band["level"]): band for band in current["bands"]}
    existing = {"employee_id": {row["employee_id_key"] for row in current["employees"]},
                "email": {row["email_key"] for row in current["employees"]}}
    occurrences = {"employee_id": defaultdict(list), "email": defaultdict(list)}
    row_errors, employees = [], []
    for row in records:
        values = {}
        for field in FIELDS:
            try:
                values[field] = validate_employee_field(field, row[field])
                if values[field] != row[field]:
                    raise ValueError("Saved value is no longer normalized")
            except ValueError as error:
                row_errors.append(issue(info["worksheet"], row["row_number"], LABELS[field], "invalid_field", str(error)))
        for field in occurrences:
            if field in values:
                key = normalized_key(values[field])
                occurrences[field][key].append(row["row_number"])
                if key in existing[field]:
                    row_errors.append(issue(info["worksheet"], row["row_number"], LABELS[field], "existing_employee",
                                            "Employee identity or email now exists; no records were inserted"))
        classification = values.get("country"), values.get("level")
        status = ("Band-matched" if classification in bands else "Unbanded") if all(classification) else None
        if status != row["band_status"]:
            row_errors.append(issue(info["worksheet"], row["row_number"], "Band Status", "band_status_changed",
                                    "Derived band status changed", expected=row["band_status"], current=status))
        if len(values) == len(FIELDS):
            employees.append(Employee(**values))
    for field, keys in occurrences.items():
        for numbers in keys.values():
            if len(numbers) > 1:
                row_errors.extend(issue(info["worksheet"], number, LABELS[field], "duplicate_in_file",
                    "Every occurrence of this duplicate must be corrected", first_row=numbers[0], duplicate_count=len(numbers)) for number in numbers)
    if employee_changes or band_changes or row_errors:
        raise ImportProblem(409, "preview_conflict", "Database or validated values changed; create a new preview",
                            retryable=False, employee_changes=employee_changes, band_changes=band_changes, errors=row_errors)
    return employees


def confirm(path, owner, preview_id):
    try:
        with transaction(path) as connection:
            info = owned(connection, owner, preview_id)
            # Completion consumes the preview once. Even an expired retry returns
            # this receipt, never revalidates or inserts the consumed rows again.
            result = saved_receipt(connection, preview_id)
            if result is not None:
                return result
            if info["status"] != "clean":
                raise ImportProblem(409, "preview_invalid", "Only a clean preview can be confirmed", retryable=False)
            if int(time.time()) >= info["expires_at"]:
                raise ImportProblem(410, "preview_expired", "Preview expired; create a new preview", retryable=False)
            employees = revalidate(connection, info)
            for employee in employees:
                insert_employee(connection, employee)
            result = {"preview_id": preview_id, "actor": normalized_key(owner), "completed_at": int(time.time()),
                      "inserted_count": len(employees), "employee_ids": [employee.employee_id for employee in employees]}
            connection.execute("INSERT INTO import_receipts VALUES (?,?,?,?,?)",
                (preview_id, result["actor"], result["completed_at"], result["inserted_count"], canonical(result["employee_ids"])))
        return result
    except ImportProblem:
        raise
    except sqlite3.IntegrityError as error:
        raise ImportProblem(409, "import_integrity_conflict", "Database rejected the batch; no partial import was committed. Create a new preview",
                            retryable=False) from error
    except sqlite3.OperationalError as error:
        if (getattr(error, "sqlite_errorcode", 0) & 255) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
            raise ImportProblem(409, "database_busy", "Database is busy; retry this same preview", retryable=True) from error
        raise ImportProblem(503, "confirmation_unavailable", "Confirmation could not be acknowledged; retry this same preview safely",
                            retryable=True) from error
    except Exception as error:
        # Includes an ambiguous COMMIT/response failure: the receipt resolves a
        # retry whether the original transaction committed or rolled back.
        raise ImportProblem(503, "confirmation_unavailable", "Confirmation could not be acknowledged; retry this same preview safely",
                            retryable=True) from error
