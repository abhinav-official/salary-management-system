"""Employee operations and one directory query shared by pagination and CSV."""

import csv
import io
from dataclasses import dataclass
from pathlib import Path

from app.database import read_transaction, transaction
from app.models import COUNTRY_CURRENCIES, LEVELS, Employee
from app.money import MAX_MINOR, format_money
from app.normalization import normalized_key
from app.repository import insert_employee


class EmployeeError(Exception):
    def __init__(self, status: int, code: str, message: str, **details):
        super().__init__(message)
        self.status = status
        self.detail = {"code": code, "message": message, **details}


_SELECT = """SELECT e.*, i.generation,
    CASE WHEN b.country IS NULL THEN 'Unbanded' ELSE 'Band-matched' END AS band_status
    FROM employees e JOIN employee_identities i ON i.employee_id=e.employee_id
    LEFT JOIN salary_bands b ON b.country=e.country AND b.level=e.level"""
_SORTS = {"employee_id": "e.employee_id_key", "name": "e.name_key", "email": "e.email_key",
          "department": "e.department_key", "country": "e.country", "level": "e.level",
          "salary": "e.salary_minor"}
# Schema restricts levels to L1..L8, whose lexical and numeric orders coincide.
CSV_HEADER = ("Employee ID", "Name", "Email", "Department", "Level", "Country", "Currency",
              "Annual Salary", "Band Status")


@dataclass(frozen=True)
class DirectoryQuery:
    search: str = ""
    country: str | None = None
    level: str | None = None
    department: str | None = None
    sort: str = "employee_id"
    direction: str = "asc"

    def __post_init__(self):
        if self.sort not in _SORTS or self.direction not in {"asc", "desc"}:
            raise ValueError("Unsupported sort or direction")
        if self.country is not None and self.country not in COUNTRY_CURRENCIES:
            raise ValueError("Unsupported country")
        if self.level is not None and self.level not in LEVELS:
            raise ValueError("Unsupported level")

    def where(self):
        clauses, values = [], []
        search = normalized_key(self.search)
        if search:
            # Search is a literal substring; user % and _ are not SQL wildcards.
            pattern = "%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            clauses.append("(e.employee_id_key LIKE ? ESCAPE '\\' OR e.name_key LIKE ? ESCAPE '\\' OR e.email_key LIKE ? ESCAPE '\\')")
            values.extend([pattern] * 3)
        for field, value in (("country", self.country), ("level", self.level),
                             ("department_key", normalized_key(self.department or ""))):
            if value:
                clauses.append(f"e.{field}=?")
                values.append(value)
        return (" WHERE " + " AND ".join(clauses) if clauses else ""), values

    def order(self):
        primary = f"{_SORTS[self.sort]} {self.direction.upper()}"
        if self.sort == "salary":
            primary = "e.country ASC, " + primary
        if self.sort != "employee_id":
            primary += ", e.employee_id_key ASC"
        return " ORDER BY " + primary

    @property
    def ordering(self):
        if self.sort == "salary":
            return f"Country ascending, salary {self.direction} within country, employee ID ascending"
        return f"{self.sort} {self.direction}" + (", employee ID ascending" if self.sort != "employee_id" else "")


def serialize_employee(row):
    result = {key: row[key] for key in ("employee_id", "name", "email", "department", "level",
                                       "country", "revision", "band_status")}
    result.update(salary_minor=str(row["salary_minor"]), salary_amount=format_money(row["salary_minor"]),
                  currency=COUNTRY_CURRENCIES[row["country"]],
                  etag=f'"{row["generation"]}:{row["revision"]}"')
    return result


def get_employee(connection, employee_id: str):
    row = connection.execute(_SELECT + " WHERE e.employee_id_key=?", (normalized_key(employee_id),)).fetchone()
    if row is None:
        raise EmployeeError(404, "employee_not_found", "Employee does not exist")
    return serialize_employee(row)


def count_employees(connection, query):
    where, values = query.where()
    return connection.execute("SELECT count(*) FROM employees e" + where, values).fetchone()[0]


def list_employees(path: Path, query: DirectoryQuery, page: int = 1, page_size: int = 25):
    if type(page) is not int or page < 1 or type(page_size) is not int or page_size not in {25, 50, 100}:
        raise ValueError("Invalid pagination")
    where, values = query.where()
    with read_transaction(path) as connection:
        total = count_employees(connection, query)
        # Avoid SQLite integer overflow for an arbitrarily large page number.
        offset = (page - 1) * page_size
        items = [] if offset >= total else [serialize_employee(row) for row in connection.execute(
            _SELECT + where + query.order() + " LIMIT ? OFFSET ?", [*values, page_size, offset])]
    return {"items": items, "total": total, "page": page, "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size, "ordering": query.ordering}


def filter_options(path: Path):
    with read_transaction(path) as connection:
        departments = [{"value": row["department_key"], "label": row["label"]} for row in connection.execute(
            "SELECT department_key, min(department COLLATE BINARY) AS label FROM employees GROUP BY department_key ORDER BY department_key")]
    return {"countries": list(COUNTRY_CURRENCIES), "levels": list(LEVELS), "departments": departments}


def _duplicates(connection, employee: Employee, *, exclude: str | None = None):
    conflicts = []
    for field, key in (("employee_id", employee.employee_id_key), ("email", employee.email_key)):
        row = connection.execute(f"SELECT employee_id_key FROM employees WHERE {field}_key=?", (key,)).fetchone()
        if row is not None and row[0] != exclude:
            conflicts.append({"field": field, "code": "duplicate", "message": f"An employee with this {field} already exists"})
    if conflicts:
        raise EmployeeError(409, "employee_conflict", "Employee ID and email must be unique", errors=conflicts)


def create_employee(path: Path, employee: Employee):
    with transaction(path) as connection:
        _duplicates(connection, employee)
        insert_employee(connection, employee)
        return get_employee(connection, employee.employee_id)


def _match(current, expected):
    if expected is None:
        raise EmployeeError(428, "revision_required", "Supply the employee's ETag in If-Match")
    if expected != current["etag"]:
        raise EmployeeError(412, "revision_conflict", "The employee changed; reload before retrying",
                            current_etag=current["etag"], current_revision=current["revision"], current=current)


def edit_employee(path: Path, employee_id: str, changes: dict, expected: str | None, country_confirmed: bool):
    with transaction(path) as connection:
        current = get_employee(connection, employee_id)
        _match(current, expected)
        if changes["country"] != current["country"] and not country_confirmed:
            raise EmployeeError(400, "country_confirmation_required",
                                "Confirm the supplied salary amount in the new country's currency; no conversion is performed",
                                currency=COUNTRY_CURRENCIES[changes["country"]])
        if current["revision"] == MAX_MINOR:
            raise EmployeeError(409, "revision_exhausted", "Employee revision cannot be incremented")
        employee = Employee(current["employee_id"], **changes, revision=current["revision"] + 1)
        _duplicates(connection, employee, exclude=employee.employee_id_key)
        connection.execute("""UPDATE employees SET name=?, email=?, department=?, level=?, country=?,
            salary_minor=?, revision=revision+1 WHERE employee_id_key=? AND revision=?""",
            (employee.name, employee.email, employee.department, employee.level, employee.country,
             employee.salary_minor, employee.employee_id_key, current["revision"]))
        return get_employee(connection, employee.employee_id)


def delete_employee(path: Path, employee_id: str, expected: str | None, confirmed: bool):
    with transaction(path) as connection:
        current = get_employee(connection, employee_id)
        _match(current, expected)
        if not confirmed:
            raise EmployeeError(400, "confirmation_required", "Confirm employee deletion")
        connection.execute("DELETE FROM employees WHERE employee_id_key=? AND revision=?",
                           (normalized_key(employee_id), current["revision"]))
        return {**current, "deleted": True}


def safe_csv_text(value: str):
    # Also catch a formula concealed behind whitespace/control characters.
    if value.startswith(("\t", "\r", "\n")) or value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def export_csv(path: Path, query: DirectoryQuery):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(CSV_HEADER)
    where, values = query.where()
    # Finish inside a single snapshot before sending a successful HTTP response.
    with read_transaction(path) as connection:
        for row in connection.execute(_SELECT + where + query.order(), values):
            writer.writerow([safe_csv_text(row[field]) for field in
                             ("employee_id", "name", "email", "department", "level", "country")] +
                            [COUNTRY_CURRENCIES[row["country"]], format_money(row["salary_minor"]), row["band_status"]])
    return output.getvalue().encode("utf-8")
