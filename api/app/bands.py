"""Band domain operations, exact wire serialization, and revision preconditions."""

import sqlite3
from pathlib import Path

from app.database import connect, transaction
from app.models import SalaryBand
from app.money import MAX_MINOR, format_money
from app.repository import insert_band


class BandError(Exception):
    def __init__(self, status: int, code: str, message: str, **details):
        super().__init__(message)
        self.status = status
        self.detail = {"code": code, "message": message, **details}


_SELECT = """SELECT b.*, i.generation,
    (SELECT count(*) FROM employees e WHERE e.country=b.country AND e.level=b.level) AS employee_count
    FROM salary_bands b JOIN band_identities i USING (country, level)"""


def serialize_band(row) -> dict:
    result = {key: row[key] for key in ("country", "level", "revision")}
    result["currency"] = SalaryBand(row["country"], row["level"], row["minimum_minor"],
                                    row["midpoint_minor"], row["maximum_minor"], row["revision"]).currency
    for field in ("minimum", "midpoint", "maximum"):
        result[f"{field}_minor"] = str(row[f"{field}_minor"])
        result[f"{field}_amount"] = format_money(row[f"{field}_minor"])
    result["etag"] = f'"{row["generation"]}:{row["revision"]}"'
    result["affected_employee_count"] = row["employee_count"]
    return result


def list_bands(path: Path) -> list[dict]:
    with connect(path, existing=True) as connection:
        return band_catalog(connection)


def band_catalog(connection: sqlite3.Connection) -> list[dict]:
    return [serialize_band(row) for row in connection.execute(_SELECT + " ORDER BY b.country, b.level")]


def get_band(connection: sqlite3.Connection, country: str, level: str) -> dict:
    row = connection.execute(_SELECT + " WHERE b.country=? AND b.level=?", (country, level)).fetchone()
    if row is None:
        raise BandError(404, "band_not_found", "No band exists for this country and level")
    return serialize_band(row)


def _match(current: dict, expected: str | None):
    if expected is None:
        raise BandError(428, "revision_required", "Supply the band's ETag in If-Match")
    if expected != current["etag"]:
        raise BandError(412, "revision_conflict", "The band changed; reload it before retrying",
                        current_etag=current["etag"], current_revision=current["revision"])


def create_band(path: Path, band: SalaryBand) -> dict:
    with transaction(path) as connection:
        if connection.execute("SELECT 1 FROM salary_bands WHERE country=? AND level=?",
                              (band.country, band.level)).fetchone():
            raise BandError(409, "band_exists", "A band already exists for this country and level")
        insert_band(connection, band)
        return get_band(connection, band.country, band.level)


def edit_band(path: Path, band: SalaryBand, expected: str | None) -> dict:
    with transaction(path) as connection:
        current = get_band(connection, band.country, band.level)
        _match(current, expected)
        if current["revision"] == MAX_MINOR:
            raise BandError(409, "revision_exhausted", "The band's revision cannot be incremented")
        connection.execute("""UPDATE salary_bands SET minimum_minor=?, midpoint_minor=?,
            maximum_minor=?, revision=revision+1 WHERE country=? AND level=? AND revision=?""",
            (band.minimum_minor, band.midpoint_minor, band.maximum_minor,
             band.country, band.level, current["revision"]))
        return get_band(connection, band.country, band.level)


def delete_band(path: Path, country: str, level: str, expected: str | None, confirmed: bool) -> dict:
    with transaction(path) as connection:
        current = get_band(connection, country, level)
        _match(current, expected)
        if not confirmed:
            raise BandError(400, "confirmation_required",
                            "Confirm deletion: affected employees will become Unbanded",
                            affected_employee_count=current["affected_employee_count"])
        connection.execute("DELETE FROM salary_bands WHERE country=? AND level=? AND revision=?",
                           (country, level, current["revision"]))
        return {"country": country, "level": level, "deleted": True,
                "affected_employee_count": current["affected_employee_count"]}
