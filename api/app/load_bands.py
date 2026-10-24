"""Explicit, all-or-nothing .xlsx band catalog loading. Never called at startup."""

import argparse
import json
import os
import posixpath
import re
import sqlite3
import sys
from collections import defaultdict
from decimal import Decimal, DecimalException
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from app.bands import BandError, band_catalog
from app.database import transaction
from app.models import COUNTRY_CURRENCIES, LEVELS, SalaryBand
from app.money import parse_money
from app.repository import insert_band

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
_HEADERS = ("Country", "Level", "Currency", "Minimum", "Midpoint", "Maximum")


class WorkbookError(ValueError):
    def __init__(self, errors: list[dict]):
        super().__init__("Salary Bands workbook validation failed")
        self.errors = errors


def _rows(path: Path):
    """Read original XML decimal lexemes; never pass money through a float."""
    with ZipFile(path) as archive:
        if sum(info.file_size for info in archive.infolist()) > 50 * 1024 * 1024:
            raise ValueError("Workbook decompressed content exceeds 50 MiB")
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        sheets = workbook.findall(f"{_NS}sheets/{_NS}sheet")
        matching = [sheet for sheet in sheets if sheet.get("name") == "Salary Bands"]
        if len(matching) != 1:
            raise ValueError("Workbook must contain exactly one Salary Bands sheet")
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {item.get("Id"): item.get("Target") for item in relationships}
        target = targets[matching[0].get(_REL)]
        if not isinstance(target, str) or not target:
            raise ValueError("Invalid worksheet relationship")
        member = posixpath.normpath(target.lstrip("/") if target.startswith("/") else "xl/" + target)
        if not member.startswith("xl/"):
            raise ValueError("Invalid worksheet relationship")
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            strings = ["".join(item.itertext()) for item in ET.fromstring(archive.read("xl/sharedStrings.xml"))]
        sheet = ET.fromstring(archive.read(member))
        for row in sheet.findall(f"{_NS}sheetData/{_NS}row"):
            cells = {}
            for cell in row.findall(f"{_NS}c"):
                address = cell.get("r", "")
                match = re.fullmatch(r"([A-Z]+)[0-9]+", address)
                if not match:
                    raise ValueError("Worksheet has an invalid cell address")
                column = match[1]
                if column in cells:
                    raise ValueError("Worksheet has duplicate cell addresses")
                kind = cell.get("t", "n")
                raw = cell.findtext(f"{_NS}v", "")
                formula = cell.find(f"{_NS}f") is not None
                if kind == "s":
                    index = int(raw)
                    if not 0 <= index < len(strings):
                        raise ValueError("Invalid shared-string index")
                    value = strings[index]
                elif kind == "inlineStr":
                    value = "".join(node.text or "" for node in cell.findall(f"{_NS}is//{_NS}t"))
                else:
                    value = raw
                cells[column] = (value, kind, formula)
            yield int(row.get("r", "0")), cells


def read_bands(path: Path) -> list[SalaryBand]:
    errors = []
    bands = []
    occurrences = defaultdict(list)
    header = None
    data_count = 0
    try:
        for row_number, cells in _rows(path):
            if not any(value.strip() or formula for value, _, formula in cells.values()):
                continue
            if header is None:
                names = {column: value.strip() for column, (value, _, formula) in cells.items() if not formula}
                if (set(names.values()) != set(_HEADERS) or len(names) != len(_HEADERS)
                        or any(formula for _, _, formula in cells.values())):
                    raise ValueError("Headers must contain Country, Level, Currency, Minimum, Midpoint, Maximum exactly once")
                header = {name: column for column, name in names.items()}
                continue
            data_count += 1
            values = {}
            row_errors = []
            for name in _HEADERS:
                value, kind, formula = cells.get(header[name], ("", "s", False))
                if formula:
                    row_errors.append({"field": name, "message": "Formulas are not allowed"})
                elif not value.strip():
                    row_errors.append({"field": name, "message": "Required value is missing"})
                elif name in {"Minimum", "Midpoint", "Maximum"}:
                    try:
                        if kind not in {"s", "inlineStr", "n"}:
                            raise ValueError("Expected exact decimal text or a numeric cell")
                        values[name] = parse_money(Decimal(value) if kind == "n" else value)
                    except (ValueError, DecimalException) as error:
                        row_errors.append({"field": name, "message": str(error)})
                elif kind not in {"s", "inlineStr"}:
                    row_errors.append({"field": name, "message": "Expected text"})
                else:
                    values[name] = value.strip()
            country, level = values.get("Country"), values.get("Level")
            if country is not None and level is not None:
                occurrences[country, level].append(row_number)
            if country is not None and country not in COUNTRY_CURRENCIES:
                row_errors.append({"field": "Country", "message": "Unsupported country"})
            if level is not None and level not in LEVELS:
                row_errors.append({"field": "Level", "message": "Level must be L1 through L8"})
            if country in COUNTRY_CURRENCIES and values.get("Currency") != COUNTRY_CURRENCIES[country]:
                row_errors.append({"field": "Currency", "message": f"Expected {COUNTRY_CURRENCIES[country]} for {country}"})
            if not row_errors:
                try:
                    bands.append(SalaryBand(country, level, values["Minimum"], values["Midpoint"], values["Maximum"]))
                except ValueError as error:
                    row_errors.append({"field": "Band", "message": str(error)})
            errors.extend({"sheet": "Salary Bands", "row": row_number, **error} for error in row_errors)
        if header is None:
            raise ValueError("Salary Bands sheet is empty")
    except (OSError, BadZipFile, ET.ParseError, KeyError, IndexError, ValueError) as error:
        if isinstance(error, WorkbookError):
            raise
        errors.append({"sheet": "Salary Bands", "row": None, "field": "Workbook", "message": str(error)})
    for key, rows in occurrences.items():
        if len(rows) > 1:
            errors.extend({"sheet": "Salary Bands", "row": row, "field": "Country/Level",
                           "message": f"Duplicate band {key[0]}/{key[1]} on Excel rows {rows}"} for row in rows)
    expected = {(country, level) for country in COUNTRY_CURRENCIES for level in LEVELS}
    present = {(band.country, band.level) for band in bands}
    if data_count != 48:
        errors.append({"sheet": "Salary Bands", "row": None, "field": "Rows", "message": f"Expected 48 bands, found {data_count}"})
    if present != expected:
        missing = sorted(f"{country}/{level}" for country, level in expected - present)
        errors.append({"sheet": "Salary Bands", "row": None, "field": "Coverage", "message": "Missing valid bands: " + ", ".join(missing)})
    if errors:
        raise WorkbookError(errors)
    return bands


def load_bands(database: Path, workbook: Path) -> dict:
    bands = read_bands(workbook)
    with transaction(database) as connection:
        existing = connection.execute("SELECT country, level, revision FROM salary_bands ORDER BY country, level").fetchall()
        if existing:
            raise BandError(409, "band_catalog_not_empty", "Band loading requires an empty catalog; nothing was changed",
                            existing_count=len(existing), existing_bands=[dict(row) for row in existing])
        for band in bands:
            insert_band(connection, band)
        catalog = band_catalog(connection)
    return {"created": len(bands), "bands": catalog}


def main() -> int:
    parser = argparse.ArgumentParser(description="Atomically load all 48 bands into an empty catalog")
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--database", type=Path,
                        default=Path(os.environ.get("DATABASE_PATH", "/var/lib/salary/salary.db")))
    args = parser.parse_args()
    try:
        print(json.dumps(load_bands(args.database, args.workbook)))
        return 0
    except WorkbookError as error:
        print(json.dumps({"code": "invalid_workbook", "errors": error.errors}), file=sys.stderr)
    except BandError as error:
        print(json.dumps(error.detail), file=sys.stderr)
    except (OSError, sqlite3.Error) as error:
        print(json.dumps({"code": "load_failed", "message": str(error)}), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
