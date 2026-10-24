import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from fastapi.testclient import TestClient

from app import imports
from app.config import Settings
from app.database import SCHEMA_VERSION, connect, transaction
from app.load_bands import load_bands
from app.main import create_app
from app.migrate import migrate
from app.models import Employee, SalaryBand
from app.passwords import hash_password
from app.repository import insert_band, insert_employee
from app.xlsx import Cell, ImportProblem, MAX_UPLOAD, NS

HEADERS = ["Employee ID", "Name", "Email", "Department", "Level", "Country", "Annual Salary"]
ROW = ["0001", "Alice", "alice@example.com", "Engineering", "L1", "India", "123.455"]
SAMPLE = Path(os.environ.get("BAND_SAMPLE_WORKBOOK", Path(__file__).parents[2] / "data/employees_10000.xlsx"))


def workbook(sheets=None):
    sheets = sheets if sheets is not None else {"Employees": {1: HEADERS, 2: ROW}}
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/></Types>')
        root = ET.Element(NS + "workbook")
        collection = ET.SubElement(root, NS + "sheets")
        rels = ET.Element("Relationships", xmlns="http://schemas.openxmlformats.org/package/2006/relationships")
        for index, (name, rows) in enumerate(sheets.items(), 1):
            ET.SubElement(collection, NS + "sheet", {"name": name, "sheetId": str(index),
                "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id": f"rId{index}"})
            ET.SubElement(rels, "Relationship", Id=f"rId{index}", Target=f"worksheets/sheet{index}.xml",
                          Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet")
            sheet = ET.Element(NS + "worksheet")
            data = ET.SubElement(sheet, NS + "sheetData")
            for number, values in rows.items():
                row = ET.SubElement(data, NS + "row", r=str(number))
                for column, value in enumerate(values, 1):
                    if value is None:
                        continue
                    cell = value if isinstance(value, Cell) else Cell(value, "inlineStr")
                    element = ET.SubElement(row, NS + "c", r=f"{chr(64+column)}{number}", t=cell.kind)
                    if cell.formula is not None:
                        ET.SubElement(element, NS + "f").text = cell.formula
                    if cell.kind == "inlineStr":
                        ET.SubElement(ET.SubElement(element, NS + "is"), NS + "t").text = cell.value
                    else:
                        ET.SubElement(element, NS + "v").text = cell.value
            archive.writestr(f"xl/worksheets/sheet{index}.xml", ET.tostring(sheet))
        archive.writestr("xl/workbook.xml", ET.tostring(root))
        archive.writestr("xl/_rels/workbook.xml.rels", ET.tostring(rels))
    return stream.getvalue()


def replace_member(contents, member, transform):
    stream = BytesIO()
    with ZipFile(BytesIO(contents)) as source, ZipFile(stream, "w") as target:
        for name in source.namelist():
            target.writestr(name, transform(source.read(name)) if name == member else source.read(name))
    return stream.getvalue()


class ImportPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password_hash = hash_password("import-test-password")

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "salary.db"
        migrate(self.path)
        self.settings = Settings(self.path, "hr@example.com", self.password_hash, "http://testserver", cookie_secure=False)
        self.client = self.enterContext(TestClient(create_app(self.settings)))
        login = self.client.post("/auth/login", headers={"Origin": "http://testserver"},
            json={"email": "hr@example.com", "password": "import-test-password"})
        self.headers = {"Origin": "http://testserver", "X-CSRF-Token": login.json()["csrf_token"], "Content-Type": "application/octet-stream"}

    def create(self, contents=None, worksheet=None):
        contents = workbook() if contents is None else contents
        response = self.client.post("/imports/previews", content=contents, headers=self.headers,
                                    params={} if worksheet is None else {"worksheet": worksheet})
        self.assertEqual(response.status_code, 201, response.text)
        result = response.json()
        self.assertEqual(result["valid_count"] + result["invalid_count"], result["total_rows"])
        self.assertEqual(result["expires_at"] - result["created_at"], 1800)
        self.assertEqual(result["source_digest"], hashlib.sha256(contents).hexdigest())
        self.assertEqual(response.headers["Location"], "/imports/previews/" + result["preview_id"])
        return result

    def page(self, info, kind="rows", **params):
        response = self.client.get(f'/imports/previews/{info["preview_id"]}/{kind}', params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def counts(self):
        with connect(self.path) as connection:
            return {table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in ("employees", "import_previews", "import_preview_rows", "import_preview_errors")}

    def test_clean_preview_normalizes_preserves_text_ids_rounds_money_and_writes_no_employees(self):
        info = self.create()
        self.assertEqual((info["status"], info["valid_count"], info["invalid_count"], info["error_count"]), ("clean", 1, 0, 0))
        self.assertEqual((info["unbanded_count"], info["valid_unbanded_count"]), (1, 1))
        self.assertEqual(info["owner"], "hr@example.com")
        self.assertRegex(info["preview_id"], r"^[A-Za-z0-9_-]{43}$")
        row = self.page(info)["items"][0]
        self.assertEqual((row["row_number"], row["values"]["employee_id"], row["values"]["salary_minor"]), (2, "0001", "12346"))
        self.assertEqual((row["salary_input"], row["salary_amount"], row["rounding_applied"]), ("123.455", "123.46", True))
        self.assertEqual((row["currency"], row["band_status"], row["band"]), ("INR", "Unbanded", None))
        self.assertEqual(row["errors"], [])
        self.assertEqual(self.page(info, "errors")["items"], [])
        self.assertEqual(self.counts()["employees"], 0)
        self.assertEqual(self.client.get(f'/imports/previews/{info["preview_id"]}').json(), info)

    def test_full_sample_inspection_selects_employees_and_previews_all_10000_with_zero_writes(self):
        contents = SAMPLE.read_bytes()
        response = self.client.post("/imports/inspect", content=contents, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        inspection = response.json()
        self.assertEqual(inspection["source_digest"], hashlib.sha256(contents).hexdigest())
        self.assertEqual([sheet["name"] for sheet in inspection["worksheets"]], ["Employees", "Salary Bands", "About"])
        self.assertFalse(inspection["selection_required"])
        self.assertEqual(inspection["selected_worksheet"], "Employees")
        self.assertEqual(inspection["worksheets"][0]["data_rows"], 10000)
        load_bands(self.path, SAMPLE)
        info = self.create(contents)
        self.assertEqual((info["status"], info["total_rows"], info["valid_count"], info["invalid_count"], info["unbanded_count"]), ("clean", 10000, 10000, 0, 0))
        self.assertEqual(info["error_count"], 0)
        self.assertEqual(self.counts()["employees"], 0)
        first = self.page(info, page_size=100)
        last = self.page(info, page=100, page_size=100)
        self.assertEqual((len(first["items"]), first["total"], first["total_pages"]), (100, 10000, 100))
        self.assertEqual((last["items"][0]["row_number"], last["items"][-1]["row_number"]), (9902, 10001))
        with connect(self.path) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM import_preview_rows WHERE valid=1 AND typeof(salary_minor)='integer' AND band_status='Band-matched'").fetchone()[0], 10000)
        self.assertTrue(all(row["band"]["revision"] == 1 and row["band"]["etag"] for row in first["items"]))

    def test_ambiguous_sheets_require_choice_and_explicit_selection_uses_actual_sheet(self):
        contents = workbook({"First": {1: HEADERS, 2: ROW}, "Second": {4: HEADERS, 8: ["second", *ROW[1:]]}})
        inspection = self.client.post("/imports/inspect", content=contents, headers=self.headers).json()
        self.assertTrue(inspection["selection_required"])
        self.assertIsNone(inspection["selected_worksheet"])
        response = self.client.post("/imports/previews", content=contents, headers=self.headers)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "worksheet_required")
        self.assertEqual(self.counts()["import_previews"], 0)
        info = self.create(contents, "Second")
        row = self.page(info)["items"][0]
        self.assertEqual((info["worksheet"], row["row_number"], row["values"]["employee_id"]), ("Second", 8, "second"))
        self.assertEqual(self.client.post("/imports/previews?worksheet=Missing", content=contents, headers=self.headers).status_code, 422)

    def test_reordered_case_insensitive_headers_and_original_excel_numbers(self):
        order = [6, 2, 5, 0, 4, 3, 1]
        contents = workbook({"Employees": {1: [], 3: [" " + HEADERS[index].upper() + " " for index in order],
            4: [], 7: [ROW[index] for index in order], 9: [], 20: ["" for _ in HEADERS]}})
        info = self.create(contents)
        row = self.page(info)["items"][0]
        self.assertEqual(info["total_rows"], 1)
        self.assertEqual((row["row_number"], row["values"]["employee_id"], row["values"]["country"]), (7, "0001", "India"))

    def test_missing_duplicate_and_unexpected_headers_are_invalid_file_errors(self):
        for headers, expected in ((HEADERS[:-1], "missing_header"), ([*HEADERS, " email "], "duplicate_header"),
                                  ([*HEADERS, "Notes"], "unexpected_header")):
            with self.subTest(headers=headers):
                info = self.create(workbook({"Employees": {1: headers, 2: ROW}}))
                self.assertEqual((info["status"], info["total_rows"]), ("invalid", 0))
                self.assertTrue(any(error["code"] == expected and error["row"] == 1 for error in self.page(info, "errors")["items"]))
        self.assertEqual(self.counts()["employees"], 0)

    def test_corrupt_missing_structure_entity_xml_and_empty_workbooks(self):
        empty_zip = BytesIO()
        with ZipFile(empty_zip, "w"):
            pass
        bad_xml = replace_member(workbook(), "xl/workbook.xml", lambda _: b"<broken")
        entity = replace_member(workbook(), "xl/workbook.xml", lambda original: b'<!DOCTYPE workbook [<!ENTITY e "bad">]>' + original)
        for contents in (b"", b"not a workbook", empty_zip.getvalue(), bad_xml, entity, workbook({})):
            with self.subTest(size=len(contents)):
                response = self.client.post("/imports/previews", content=contents, headers=self.headers)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json()["detail"]["code"], "invalid_workbook")
        for contents in (workbook({"Empty": {}}), workbook({"Headers only": {1: HEADERS}})):
            info = self.create(contents)
            self.assertEqual((info["status"], info["total_rows"], info["valid_count"]), ("invalid", 0, 0))
            self.assertGreater(info["error_count"], 0)
        self.assertEqual(self.counts()["employees"], 0)

    def test_partially_blank_rows_report_all_field_errors_and_not_skip_rows(self):
        bad = ["id", None, "invalid", " ", "L9", "France", "-0.001"]
        info = self.create(workbook({"Employees": {1: HEADERS, 2: [], 5: bad, 9: ROW}}))
        self.assertEqual((info["total_rows"], info["valid_count"], info["invalid_count"]), (2, 1, 1))
        rows = self.page(info)["items"]
        self.assertEqual([row["row_number"] for row in rows], [5, 9])
        self.assertEqual({error["field"] for error in rows[0]["errors"]}, {"Name", "Email", "Department", "Level", "Country", "Annual Salary"})
        self.assertIsNone(rows[0]["band_status"])
        self.assertEqual(info["error_count"], 6)
        self.assertEqual(info["unbanded_count"], 1)

    def test_casefold_duplicates_mark_every_participant_even_with_other_errors(self):
        first = [" Straße ", *ROW[1:]]
        second = ["STRASSE", "Second", " ALICE@EXAMPLE.COM ", *ROW[3:-1], "-0.001"]
        third = ["strasse", "Third", "third@example.com", *ROW[3:]]
        info = self.create(workbook({"Employees": {1: HEADERS, 2: first, 30: second, 80: third}}))
        self.assertEqual((info["valid_count"], info["invalid_count"]), (0, 3))
        errors = self.page(info, "errors", page_size=100)["items"]
        duplicate_ids = [error for error in errors if error["field"] == "Employee ID" and error["code"] == "duplicate_in_file"]
        self.assertEqual({error["row"] for error in duplicate_ids}, {2, 30, 80})
        self.assertTrue(all(error["duplicate_count"] == 3 for error in duplicate_ids))
        self.assertEqual({error["row"] for error in errors if error["field"] == "Email" and error["code"] == "duplicate_in_file"}, {2, 30})
        self.assertTrue(any(error["row"] == 30 and error["field"] == "Annual Salary" for error in errors))
        self.assertEqual(self.counts()["employees"], 0)

    def test_existing_id_email_conflicts_are_casefolded_and_never_overwrite_or_skip(self):
        employee = Employee("Straße", "Original", "Alice@example.com", "Sales", "L1", "India", 99999)
        with transaction(self.path) as connection:
            insert_employee(connection, employee)
        before = self.client.get("/employees/records/STRASSE").json()
        info = self.create(workbook({"Employees": {1: HEADERS, 2: [" STRASSE ", "Changed", " ALICE@EXAMPLE.COM ", *ROW[3:]]}}))
        self.assertEqual((info["status"], info["invalid_count"]), ("invalid", 1))
        errors = self.page(info, "errors")["items"]
        self.assertEqual({error["field"] for error in errors if error["code"] == "existing_employee"}, {"Employee ID", "Email"})
        self.assertEqual(self.client.get("/employees/records/STRASSE").json(), before)
        self.assertEqual(self.counts()["employees"], 1)

    def test_formulas_with_cached_values_headers_and_unmapped_columns_are_rejected(self):
        contents = workbook({"Employees": {1: HEADERS, 2: [*ROW[:-1], Cell("123.45", "n", "1+1")],
            3: ["other", "Other", "other@example.com", *ROW[3:], Cell("1", "n", "SUM(A1)")]}})
        inspection = self.client.post("/imports/inspect", content=contents, headers=self.headers).json()
        self.assertEqual(inspection["worksheets"][0]["formula_count"], 2)
        info = self.create(contents)
        self.assertEqual((info["valid_count"], info["invalid_count"]), (0, 2))
        self.assertEqual({error["row"] for error in self.page(info, "errors")["items"] if error["code"] == "formula"}, {2, 3})
        header_formula = self.create(workbook({"Employees": {1: [Cell("Employee ID", "inlineStr", '"Employee ID"'), *HEADERS[1:]], 2: ROW}}))
        self.assertEqual(header_formula["status"], "invalid")
        self.assertTrue(any(error["code"] == "formula" and error["row"] == 1 for error in self.page(header_formula, "errors")["items"]))

    def test_raw_numeric_decimal_exponents_half_up_range_and_negative_before_rounding(self):
        for index, (lexeme, expected) in enumerate((("123.455", "12346"), ("1.23455E2", "12346"),
            ("92233720368547758.07", "9223372036854775807"), ("0.005", "1"), ("0", "0"))):
            with self.subTest(lexeme=lexeme):
                info = self.create(workbook({"Employees": {1: HEADERS, 2: [f"id{index}", *ROW[1:-1], Cell(lexeme, "n")]}}))
                self.assertEqual(info["status"], "clean")
                self.assertEqual(self.page(info)["items"][0]["values"]["salary_minor"], expected)
        for lexeme in ("-0.001", "NaN", "Infinity", "92233720368547758.08"):
            info = self.create(workbook({"Employees": {1: HEADERS, 2: [*ROW[:-1], Cell(lexeme, "n")]}}))
            self.assertEqual((info["status"], info["invalid_count"]), ("invalid", 1))
        self.assertEqual(self.counts()["employees"], 0)

    def test_normalization_of_text_and_unsupported_cell_types_are_explicit(self):
        info = self.create(workbook({"Employees": {1: HEADERS, 2: [" 00001 ", " Alice ", " Alice@example.com ", " Engineering ", " L1 ", " India ", "100.00"]}}))
        row = self.page(info)["items"][0]
        self.assertEqual((row["values"]["employee_id"], row["values"]["name"], row["rounding_applied"]), ("00001", "Alice", False))
        for column, cell in ((0, Cell("1", "n")), (6, Cell("1", "b")), (6, Cell("#VALUE!", "e"))):
            values = list(ROW)
            values[column] = cell
            bad = self.create(workbook({"Employees": {1: HEADERS, 2: values}}))
            self.assertEqual(bad["invalid_count"], 1)

    def test_missing_bands_and_out_of_band_salaries_are_valid_and_snapshotted(self):
        before = self.create()
        old_row = self.page(before)["items"][0]
        with transaction(self.path) as connection:
            insert_band(connection, SalaryBand("India", "L1", 100000, 200000, 300000))
        matched = self.create()
        matched_row = self.page(matched)["items"][0]
        self.assertEqual((matched["status"], matched["unbanded_count"], matched_row["band_status"]), ("clean", 0, "Band-matched"))
        self.assertEqual(matched_row["band"]["minimum_minor"], "100000")
        self.assertEqual(matched_row["band"]["revision"], 1)
        with transaction(self.path) as connection:
            connection.execute("UPDATE salary_bands SET midpoint_minor=250000, revision=revision+1")
        changed = self.create()
        self.assertEqual(self.page(changed)["items"][0]["band"]["revision"], 2)
        with transaction(self.path) as connection:
            connection.execute("DELETE FROM salary_bands")
        self.assertEqual(self.page(matched)["items"][0], matched_row)
        self.assertEqual(self.page(before)["items"][0], old_row)
        self.assertEqual(before["source_digest"], matched["source_digest"])
        self.assertNotEqual(before["proposed_digest"], matched["proposed_digest"])
        self.assertNotEqual(changed["proposed_digest"], matched["proposed_digest"])

    def test_errors_and_invalid_rows_beyond_first_page_are_completely_validated(self):
        rows = {1: HEADERS}
        for number in range(2, 122):
            rows[number] = [f"id{number}", "Name", f"person{number}@example.com", *ROW[3:-1], "100.00" if number != 102 else "-0.001"]
        info = self.create(workbook({"Employees": rows}))
        self.assertEqual((info["total_rows"], info["valid_count"], info["invalid_count"], info["error_count"]), (120, 119, 1, 1))
        first = self.page(info, page_size=100)
        self.assertTrue(all(row["valid"] for row in first["items"]))
        second = self.page(info, page=2, page_size=100)
        self.assertEqual(second["items"][0]["row_number"], 102)
        self.assertFalse(second["items"][0]["valid"])
        self.assertEqual(self.page(info, "errors")["items"][0]["row"], 102)
        for number in range(2, 122):
            rows[number][2] = "invalid"
            rows[number][-1] = "-0.001"
        all_bad = self.create(workbook({"Employees": rows}))
        self.assertEqual(all_bad["invalid_count"], 120)
        self.assertGreaterEqual(all_bad["error_count"], 240)
        collected = []
        pages = self.page(all_bad, "errors", page_size=100)["total_pages"]
        for page in range(1, pages + 1):
            collected += self.page(all_bad, "errors", page=page, page_size=100)["items"]
        self.assertEqual(len(collected), all_bad["error_count"])
        self.assertEqual({error["row"] for error in collected}, set(range(2, 122)))
        for size in (25, 50, 100):
            page = self.page(all_bad, page_size=size)
            self.assertEqual((len(page["items"]), page["total"]), (size, 120))
        self.assertEqual(self.page(all_bad, page=9999)["items"], [])
        self.assertEqual(self.page(all_bad, "errors", page=9999)["items"], [])

    def test_ownership_new_login_expiry_boundaries_and_no_confirmation_endpoint(self):
        info = self.create()
        preview_id = info["preview_id"]
        other_settings = Settings(self.path, "other@example.com", self.password_hash, "http://testserver", cookie_secure=False)
        with TestClient(create_app(other_settings)) as other:
            other.cookies.update(self.client.cookies)
            for suffix in ("", "/rows", "/errors"):
                self.assertEqual(other.get(f"/imports/previews/{preview_id}{suffix}").status_code, 404)
        with patch("app.imports.time.time", return_value=info["expires_at"] - 1):
            self.assertEqual(self.client.get(f"/imports/previews/{preview_id}").status_code, 200)
        with patch("app.imports.time.time", return_value=info["expires_at"]):
            for suffix in ("", "/rows", "/errors"):
                response = self.client.get(f"/imports/previews/{preview_id}{suffix}")
                self.assertEqual(response.status_code, 410)
                self.assertEqual(response.json()["detail"]["code"], "preview_expired")
        with connect(self.path) as connection:
            self.assertEqual(connection.execute("SELECT status FROM import_previews").fetchone()[0], "clean")
        self.assertEqual(self.client.post(f"/imports/previews/{preview_id}/confirm", headers=self.headers).status_code, 404)
        self.assertFalse(any("confirm" in path for path in self.client.app.openapi()["paths"]))
        self.assertEqual(self.counts()["employees"], 0)

    def test_immutable_rows_errors_metadata_and_additions_after_seal(self):
        info = self.create(workbook({"Employees": {1: HEADERS, 2: [*ROW[:-1], "-1"]}}))
        statements = ("UPDATE import_previews SET status='clean'", "UPDATE import_previews SET sealed=0",
            "DELETE FROM import_previews", "UPDATE import_preview_rows SET salary_minor=1", "DELETE FROM import_preview_rows",
            "UPDATE import_preview_errors SET message='changed'", "DELETE FROM import_preview_errors",
            "INSERT INTO import_preview_rows SELECT * FROM import_preview_rows",
            "INSERT INTO import_preview_errors SELECT * FROM import_preview_errors")
        for statement in statements:
            with self.subTest(statement=statement), self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                with transaction(self.path) as connection:
                    connection.execute(statement)
        self.assertEqual(self.client.get(f'/imports/previews/{info["preview_id"]}').json(), info)

    def test_failed_snapshot_storage_rolls_back_all_preview_tables_and_employee_count(self):
        with connect(self.path) as connection:
            connection.execute("CREATE TRIGGER simulate_failure BEFORE INSERT ON import_preview_rows WHEN NEW.row_number=3 BEGIN SELECT RAISE(ABORT,'simulated failure'); END")
        contents = workbook({"Employees": {1: HEADERS, 2: ROW, 3: ["other", "Other", "other@example.com", *ROW[3:]]}})
        with self.assertRaises(sqlite3.IntegrityError):
            imports.create_preview(self.path, "hr@example.com", contents)
        self.assertEqual(self.counts(), {"employees": 0, "import_previews": 0, "import_preview_rows": 0, "import_preview_errors": 0})

    def test_restart_migrations_and_new_session_preserve_snapshot(self):
        info = self.create()
        rows = self.page(info)
        result = subprocess.run([sys.executable, "-m", "app.migrate"], cwd=Path(__file__).parents[1],
            env={**os.environ, "DATABASE_PATH": str(self.path)}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        with TestClient(create_app(self.settings)) as restarted:
            restarted.post("/auth/login", headers={"Origin": "http://testserver"}, json={"email": "hr@example.com", "password": "import-test-password"})
            self.assertEqual(restarted.get(f'/imports/previews/{info["preview_id"]}').json(), info)
            self.assertEqual(restarted.get(f'/imports/previews/{info["preview_id"]}/rows').json(), rows)
        self.assertEqual(self.counts()["employees"], 0)

    def test_digests_are_reproducible_and_changed_proposals_get_new_opaque_ids(self):
        first = self.create()
        second = self.create()
        self.assertNotEqual(first["preview_id"], second["preview_id"])
        self.assertEqual(first["source_digest"], second["source_digest"])
        self.assertEqual(first["proposed_digest"], second["proposed_digest"])
        changed = self.create(workbook({"Employees": {1: HEADERS, 2: [*ROW[:-1], "124.00"]}}))
        self.assertNotEqual(first["source_digest"], changed["source_digest"])
        self.assertNotEqual(first["proposed_digest"], changed["proposed_digest"])

    def test_upload_limits_chunked_uploads_content_type_auth_and_csrf(self):
        with TestClient(create_app(self.settings)) as anonymous:
            self.assertEqual(anonymous.post("/imports/inspect", content=workbook(), headers={"Origin": "http://testserver"}).status_code, 401)
        self.assertEqual(self.client.post("/imports/previews", content=workbook(), headers={"Origin": "http://testserver"}).status_code, 403)
        self.assertEqual(self.client.post("/imports/previews", content=workbook(), headers={**self.headers, "Origin": "https://other.example"}).status_code, 403)
        self.assertEqual(self.client.post("/imports/previews", content=workbook(), headers={**self.headers, "Content-Type": "application/json"}).status_code, 415)
        self.assertEqual(self.client.post("/imports/previews", content=b"x" * (MAX_UPLOAD + 1), headers=self.headers).status_code, 413)
        with patch("app.import_routes.MAX_UPLOAD", 100):
            response = self.client.post("/imports/previews", content=iter([b"x" * 80, b"y" * 80]), headers=self.headers)
            self.assertEqual(response.status_code, 413)
        with patch("app.xlsx.MAX_EXPANDED", 100):
            self.assertEqual(self.client.post("/imports/previews", content=workbook(), headers=self.headers).status_code, 413)
        info = self.create()
        for kind in ("rows", "errors"):
            for params in ({"page": 0}, {"page_size": 10}, {"page": "x"}, {"unknown": "x"}):
                self.assertEqual(self.client.get(f'/imports/previews/{info["preview_id"]}/{kind}', params=params).status_code, 422)
        self.assertEqual(self.client.get(f'/imports/previews/{info["preview_id"]}').headers["cache-control"], "no-store")

    def test_duplicate_or_invalid_xml_cell_coordinates_are_corrupt_not_partial(self):
        bad = replace_member(workbook(), "xl/worksheets/sheet1.xml", lambda contents: contents.replace(b'r="B2"', b'r="A2"'))
        self.assertEqual(self.client.post("/imports/previews", content=bad, headers=self.headers).status_code, 422)
        self.assertEqual(self.counts()["import_previews"], 0)

    def test_v4_upgrade_preserves_employee_band_session_and_then_preview(self):
        legacy = Path(self.directory.name) / "legacy.db"
        migration_dir = Path(__file__).parents[1] / "app/migrations"
        with connect(legacy) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            for migration in sorted(migration_dir.glob("*.sql"))[:4]:
                connection.executescript(migration.read_text())
            connection.execute("PRAGMA user_version=4")
            insert_employee(connection, Employee("old", "Name", "old@example.com", "Sales", "L1", "US", 12346))
            insert_band(connection, SalaryBand("US", "L1", 0, 100, 200))
            connection.execute("INSERT INTO sessions VALUES ('old', 'csrf', 2000000000)")
        migrate(legacy)
        info = imports.create_preview(legacy, "hr@example.com", workbook())
        migrate(legacy)
        with connect(legacy) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            self.assertEqual(tuple(connection.execute("SELECT employee_id,salary_minor FROM employees").fetchone()), ("old", 12346))
            self.assertEqual(connection.execute("SELECT count(*) FROM salary_bands").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT token_hash FROM sessions").fetchone()[0], "old")
        self.assertEqual(imports.preview(legacy, "hr@example.com", info["preview_id"]), info)
