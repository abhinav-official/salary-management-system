"""Bounded XLSX inspection from original XML text, without float conversions."""

from dataclasses import dataclass
from io import BytesIO
import posixpath
import re
import zlib
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
MAX_UPLOAD = 10 * 1024 * 1024
MAX_EXPANDED = 50 * 1024 * 1024
MAX_SHEETS = 32
MAX_DATA_ROWS = 20000
HEADERS = {"Employee ID": "employee_id", "Name": "name", "Email": "email", "Department": "department",
           "Level": "level", "Country": "country", "Annual Salary": "salary_minor"}


class ImportProblem(Exception):
    def __init__(self, status, code, message, **details):
        super().__init__(message)
        self.status = status
        self.detail = {"code": code, "message": message, **details}


@dataclass(frozen=True)
class Cell:
    value: str
    kind: str
    formula: str | None = None


@dataclass(frozen=True)
class Sheet:
    name: str
    rows: list[tuple[int, dict[str, Cell]]]


def issue(sheet, row, field, code, message, **extra):
    return {"sheet": sheet, "row": row, "field": field, "code": code, "message": message, **extra}


def nonblank(cells):
    return any(cell.value.strip() or cell.formula is not None for cell in cells.values())


def xml(archive, member):
    text = archive.read(member).decode("utf-8-sig")
    if "\x00" in text or "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("Unsupported XML encoding or entity declaration")
    return ET.fromstring(text)


def column_name(number):
    text = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        text = chr(65 + remainder) + text
    return text


def column_number(text):
    result = 0
    for character in text:
        result = result * 26 + ord(character) - 64
    return result


def read_workbook(contents: bytes) -> list[Sheet]:
    if len(contents) > MAX_UPLOAD:
        raise ImportProblem(413, "upload_limit", "Workbook exceeds 10 MiB")
    try:
        with ZipFile(BytesIO(contents)) as archive:
            members = archive.infolist()
            if len(members) > 4096 or sum(item.file_size for item in members) > MAX_EXPANDED:
                raise ImportProblem(413, "workbook_limit", "Workbook expanded content exceeds its limits")
            if len({item.filename for item in members}) != len(members) or any(item.flag_bits & 1 for item in members):
                raise ValueError("Duplicate ZIP members or encryption are not supported")
            types = xml(archive, "[Content_Types].xml")
            main_type = next((item.get("ContentType") for item in types if item.get("PartName") == "/xl/workbook.xml"), None)
            if main_type != "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml":
                raise ValueError("Expected an .xlsx workbook")
            workbook = xml(archive, "xl/workbook.xml")
            if workbook.tag != NS + "workbook":
                raise ValueError("Unsupported workbook XML")
            sheets = workbook.findall(f"{NS}sheets/{NS}sheet")
            if not sheets or len(sheets) > MAX_SHEETS:
                raise ValueError("Workbook must contain 1 to 32 worksheets")
            relationships = {item.get("Id"): item for item in xml(archive, "xl/_rels/workbook.xml.rels")}
            strings = []
            if "xl/sharedStrings.xml" in archive.namelist():
                strings = ["".join(node.text or "" for node in item.iter(NS + "t"))
                           for item in xml(archive, "xl/sharedStrings.xml")]
            result, names = [], set()
            for sheet in sheets:
                name = sheet.get("name", "")
                if not name or name.casefold() in names:
                    raise ValueError("Worksheet names must be nonempty and unique")
                names.add(name.casefold())
                relationship = relationships[sheet.get(REL)]
                if relationship.get("TargetMode") == "External" or not relationship.get("Type", "").endswith("/worksheet"):
                    raise ValueError("Unsupported worksheet relationship")
                target = relationship.get("Target", "")
                member = posixpath.normpath(target.lstrip("/") if target.startswith("/") else "xl/" + target)
                if not target or not member.startswith("xl/"):
                    raise ValueError("Invalid worksheet relationship")
                root = xml(archive, member)
                if root.tag != NS + "worksheet":
                    raise ValueError("Invalid worksheet XML")
                rows, previous, used = [], 0, 0
                for row in root.findall(f"{NS}sheetData/{NS}row"):
                    number = int(row.get("r", previous + 1))
                    if not previous < number <= 1048576:
                        raise ValueError("Invalid or duplicate Excel row number")
                    previous = number
                    cells, next_column = {}, 1
                    for cell in row.findall(NS + "c"):
                        address = cell.get("r", f"{column_name(next_column)}{number}")
                        match = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]*)", address)
                        if not match or int(match[2]) != number:
                            raise ValueError("Invalid cell address")
                        column = match[1]
                        next_column = column_number(column) + 1
                        if next_column > 16385 or column in cells:
                            raise ValueError("Invalid or duplicate cell column")
                        kind = cell.get("t", "n")
                        value = cell.findtext(NS + "v", "")
                        if kind == "s":
                            index = int(value)
                            if not 0 <= index < len(strings):
                                raise ValueError("Invalid shared-string index")
                            value = strings[index]
                        elif kind == "inlineStr":
                            value = "".join(node.text or "" for node in cell.findall(f"{NS}is//{NS}t"))
                        formula = cell.find(NS + "f")
                        cells[column] = Cell(value, kind, "".join(formula.itertext()) if formula is not None else None)
                    if nonblank(cells):
                        used += 1
                        if used > MAX_DATA_ROWS + 1:
                            raise ImportProblem(413, "row_limit", "A worksheet exceeds 20,000 data rows")
                        rows.append((number, cells))
                result.append(Sheet(name, rows))
            return result
    except ImportProblem:
        raise
    except (BadZipFile, OSError, KeyError, ValueError, ET.ParseError, RuntimeError, NotImplementedError, zlib.error) as error:
        raise ImportProblem(422, "invalid_workbook", "Workbook is corrupt, empty or unsupported") from error


def header(sheet):
    if not sheet.rows:
        return None, {}, [issue(sheet.name, None, "Headers", "missing_headers", "Worksheet is empty")]
    number, cells = sheet.rows[0]
    allowed = {name.casefold(): (name, field) for name, field in HEADERS.items()}
    mapping, occurrences, errors = {}, {}, []
    for column, cell in cells.items():
        if not cell.value.strip() and cell.formula is None:
            continue
        if cell.formula is not None:
            errors.append(issue(sheet.name, number, column, "formula", "Formulas are not allowed"))
            continue
        key = cell.value.strip().casefold()
        occurrences.setdefault(key, []).append(column)
        if cell.kind not in {"s", "inlineStr"} or key not in allowed:
            errors.append(issue(sheet.name, number, column, "unexpected_header", "Expected only the approved employee headers"))
        else:
            mapping[column] = allowed[key][1]
    for key, columns in occurrences.items():
        if len(columns) > 1:
            errors.append(issue(sheet.name, number, allowed[key][0] if key in allowed else "Headers", "duplicate_header",
                                "Header appears more than once", columns=columns))
    for key, (label, _) in allowed.items():
        if key not in occurrences:
            errors.append(issue(sheet.name, number, label, "missing_header", f"Missing required header: {label}"))
    return number, mapping, errors


def inspection(sheets):
    candidates, items = [], []
    for sheet in sheets:
        number, _, errors = header(sheet)
        if not errors:
            candidates.append(sheet.name)
        items.append({"name": sheet.name, "header_row": number, "data_rows": max(0, len(sheet.rows) - 1),
                      "candidate": not errors, "header_errors": errors,
                      "formula_count": sum(cell.formula is not None for _, cells in sheet.rows for cell in cells.values())})
    choices = candidates or [sheet.name for sheet in sheets]
    return {"worksheets": items, "selection_required": len(choices) != 1,
            "selected_worksheet": choices[0] if len(choices) == 1 else None}


def choose_sheet(sheets, selected):
    if selected is not None:
        for sheet in sheets:
            if sheet.name == selected:
                return sheet
        raise ImportProblem(422, "worksheet_not_found", "Choose a worksheet from this workbook")
    info = inspection(sheets)
    if info["selection_required"]:
        raise ImportProblem(409, "worksheet_required", "Choose a worksheet before creating a preview", worksheets=info["worksheets"])
    return next(sheet for sheet in sheets if sheet.name == info["selected_worksheet"])
