import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from fastapi.testclient import TestClient

from app.bands import BandError, list_bands
from app.config import Settings
from app.database import connect, transaction
from app.load_bands import WorkbookError, load_bands, read_bands
from app.main import create_app
from app.migrate import migrate
from app.models import Employee, SalaryBand
from app.passwords import hash_password
from app.repository import insert_band, insert_employee

SAMPLE = Path(os.environ.get("BAND_SAMPLE_WORKBOOK",
                            Path(__file__).parents[2] / "data/employees_10000.xlsx"))
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
# Independent expected values from data/README.md, not the generator/parser.
BASES = {"India": ("INR", 600000), "US": ("USD", 55000), "UK": ("GBP", 30000),
         "Germany": ("EUR", 42000), "Singapore": ("SGD", 48000), "UAE": ("AED", 90000)}
MULTIPLIERS = (100, 135, 180, 240, 320, 425, 560, 740)


class BandLoaderTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "salary.db"
        migrate(self.path)

    def mutate(self, cells=None, transform=None):
        output = Path(self.directory.name) / f"modified_{len(list(Path(self.directory.name).glob('*.xlsx')))}.xlsx"
        with ZipFile(SAMPLE) as source, ZipFile(output, "w") as target:
            for name in source.namelist():
                contents = source.read(name)
                if name == "xl/worksheets/sheet2.xml":
                    root = ET.fromstring(contents)
                    for address, (value, kind) in (cells or {}).items():
                        cell = root.find(f'.//{NS}c[@r="{address}"]')
                        cell.clear()
                        cell.set("r", address)
                        if kind == "text":
                            cell.set("t", "inlineStr")
                            ET.SubElement(ET.SubElement(cell, NS + "is"), NS + "t").text = value
                        else:
                            cell.set("t", "n")
                            if kind == "formula":
                                ET.SubElement(cell, NS + "f").text = "1+1"
                            ET.SubElement(cell, NS + "v").text = value
                    if transform:
                        transform(root)
                    contents = ET.tostring(root)
                target.writestr(name, contents)
        return output

    def count(self, table="salary_bands"):
        with connect(self.path) as connection:
            return connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

    def test_all_48_pairs_exact_values_currencies_and_no_employee_loading(self):
        result = load_bands(self.path, SAMPLE)
        self.assertEqual(result["created"], 48)
        actual = {(item["country"], item["level"]): item for item in result["bands"]}
        self.assertEqual(len(actual), 48)
        for country, (currency, base) in BASES.items():
            for number, multiplier in enumerate(MULTIPLIERS, 1):
                with self.subTest(country=country, level=number):
                    item = actual[country, f"L{number}"]
                    midpoint = base * multiplier
                    self.assertEqual(item["currency"], currency)
                    self.assertEqual(item["minimum_minor"], str(midpoint * 80 // 100))
                    self.assertEqual(item["midpoint_minor"], str(midpoint))
                    self.assertEqual(item["maximum_minor"], str(midpoint * 120 // 100))
                    self.assertEqual(item["revision"], 1)
        self.assertEqual(self.count("employees"), 0)
        self.assertEqual(self.count("band_identities"), 48)

    def test_existing_partial_catalog_conflict_reports_keys_and_preserves_data(self):
        with transaction(self.path) as connection:
            insert_band(connection, SalaryBand("UK", "L3", 100, 200, 300))
        before = list_bands(self.path)
        with self.assertRaises(BandError) as caught:
            load_bands(self.path, SAMPLE)
        self.assertEqual(caught.exception.detail["code"], "band_catalog_not_empty")
        self.assertEqual(caught.exception.detail["existing_count"], 1)
        self.assertEqual(caught.exception.detail["existing_bands"],
                         [{"country": "UK", "level": "L3", "revision": 1}])
        self.assertEqual(list_bands(self.path), before)

    def test_v2_upgrade_backfills_identity_without_changing_existing_band_or_employee(self):
        with transaction(self.path) as connection:
            insert_band(connection, SalaryBand("UK", "L3", 100, 200, 300, revision=7))
            insert_employee(connection, Employee("0001", "Name", "person@example.com", "Sales", "L3", "UK", 500))
            connection.execute("DROP TRIGGER salary_bands_identity")
            connection.execute("DROP TABLE band_identities")
            connection.execute("DROP TRIGGER employees_identity")
            connection.execute("DROP TABLE employee_identities")
            for table in ("import_preview_errors", "import_preview_rows", "import_previews"):
                connection.execute(f"DROP TABLE {table}")
            connection.execute("PRAGMA user_version=2")
        migrate(self.path)
        result = list_bands(self.path)
        self.assertEqual(len(result), 1)
        self.assertEqual((result[0]["country"], result[0]["level"], result[0]["revision"]), ("UK", "L3", 7))
        self.assertEqual((result[0]["minimum_minor"], result[0]["midpoint_minor"], result[0]["maximum_minor"]), ("100", "200", "300"))
        self.assertEqual(result[0]["affected_employee_count"], 1)
        before = result[0]["etag"]
        migrate(self.path)
        self.assertEqual(list_bands(self.path)[0]["etag"], before)

    def test_duplicate_bands_report_both_excel_rows_and_insert_nothing(self):
        workbook = self.mutate({"A49": ("India", "text"), "B49": ("L1", "text"),
                                "C49": ("INR", "text")})
        with self.assertRaises(WorkbookError) as caught:
            load_bands(self.path, workbook)
        rows = {error["row"] for error in caught.exception.errors if error["field"] == "Country/Level"}
        self.assertEqual(rows, {2, 49})
        self.assertEqual(self.count(), 0)

    def test_every_invalid_row_is_reported_and_no_rows_are_inserted(self):
        workbook = self.mutate({"D2": ("-0.001", "text"), "E3": ("0", "text"),
                                "C49": ("USD", "text")})
        with self.assertRaises(WorkbookError) as caught:
            load_bands(self.path, workbook)
        self.assertTrue({2, 3, 49}.issubset({error["row"] for error in caught.exception.errors}))
        self.assertEqual(self.count(), 0)
        self.assertEqual(self.count("band_identities"), 0)

    def test_numeric_xml_is_parsed_exactly_and_rounded_half_up(self):
        workbook = self.mutate({"E2": ("600000.005", "numeric")})
        bands = read_bands(workbook)
        self.assertEqual(bands[0].midpoint_minor, 60000001)

    def test_formulas_with_cached_values_are_rejected(self):
        workbook = self.mutate({"D2": ("480000", "formula")})
        with self.assertRaises(WorkbookError) as caught:
            load_bands(self.path, workbook)
        self.assertTrue(any(error["row"] == 2 and "Formulas" in error["message"]
                            for error in caught.exception.errors))
        self.assertEqual(self.count(), 0)

    def test_missing_pairs_and_invalid_headers_are_rejected(self):
        for workbook in (self.mutate({"A1": ("Level", "text")}),
                         self.mutate(transform=lambda root: root.find(NS + "sheetData").remove(
                             root.find(f'{NS}sheetData/{NS}row[@r="49"]')))):
            with self.subTest(workbook=workbook), self.assertRaises(WorkbookError):
                load_bands(self.path, workbook)
        self.assertEqual(self.count(), 0)

    def test_storage_failure_after_inserts_rolls_back_entire_catalog(self):
        with connect(self.path) as connection:
            connection.execute("""CREATE TRIGGER fail_mid_load BEFORE INSERT ON salary_bands
                WHEN NEW.country='Germany' BEGIN SELECT RAISE(ABORT, 'simulated insert failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            load_bands(self.path, SAMPLE)
        self.assertEqual(self.count(), 0)
        self.assertEqual(self.count("band_identities"), 0)

    def test_concurrent_loaders_create_catalog_once_and_other_reports_conflict(self):
        def attempt():
            try:
                return load_bands(self.path, SAMPLE)["created"]
            except BandError as error:
                return error.detail["code"]
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(lambda _: attempt(), range(2)))
        self.assertCountEqual(outcomes, [48, "band_catalog_not_empty"])
        self.assertEqual(self.count(), 48)

    def test_cli_reports_success_conflict_and_invalid_workbook_with_exit_codes(self):
        command = [sys.executable, "-m", "app.load_bands", str(SAMPLE), "--database", str(self.path)]
        first = subprocess.run(command, cwd=Path(__file__).parents[1], capture_output=True, text=True)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(json.loads(first.stdout)["created"], 48)
        second = subprocess.run(command, cwd=Path(__file__).parents[1], capture_output=True, text=True)
        self.assertEqual(second.returncode, 1)
        self.assertEqual(json.loads(second.stderr)["existing_count"], 48)
        invalid = subprocess.run([*command[:3], "/missing/workbook.xlsx", *command[4:]],
                                 cwd=Path(__file__).parents[1], capture_output=True, text=True)
        self.assertEqual(invalid.returncode, 1)
        self.assertEqual(json.loads(invalid.stderr)["code"], "invalid_workbook")


class BandAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password = "band-test-password"
        cls.password_hash = hash_password(cls.password)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "salary.db"
        migrate(self.path)
        self.settings = Settings(self.path, "hr@example.com", self.password_hash,
                                 "http://testserver", cookie_secure=False)
        self.client = self.enterContext(TestClient(create_app(self.settings)))
        result = self.client.post("/auth/login", headers={"Origin": "http://testserver"},
                                  json={"email": "hr@example.com", "password": self.password})
        self.headers = {"Origin": "http://testserver", "X-CSRF-Token": result.json()["csrf_token"]}
        self.payload = {"country": "India", "level": "L1", "minimum_amount": "100.00",
                        "midpoint_amount": "200.00", "maximum_amount": "300.00"}
        self.amounts = {key: value for key, value in self.payload.items() if key.endswith("_amount")}

    def create(self):
        response = self.client.post("/bands", json=self.payload, headers=self.headers)
        self.assertEqual(response.status_code, 201, response.text)
        return response

    def test_list_create_detail_and_duplicate_conflict(self):
        self.assertEqual(self.client.get("/bands").json(), {"items": [], "total": 0})
        created = self.create()
        self.assertEqual(created.json()["currency"], "INR")
        self.assertEqual(created.json()["minimum_minor"], "10000")
        self.assertEqual(created.headers["etag"], created.json()["etag"])
        self.assertEqual(self.client.get("/bands/India/L1").json(), created.json())
        duplicate = self.client.post("/bands", json=self.payload, headers=self.headers)
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(duplicate.json()["detail"]["code"], "band_exists")
        self.assertEqual(self.client.get("/bands").json()["total"], 1)

    def test_exact_money_rounding_input_types_currency_and_validation(self):
        rounded = self.client.post("/bands", headers=self.headers,
            json={**self.payload, "minimum_amount": "123.455"})
        self.assertEqual(rounded.json()["minimum_minor"], "12346")
        self.assertEqual(rounded.json()["minimum_amount"], "123.46")
        for changes in ({"country": "France"}, {"level": "L9"}, {"currency": "USD"},
                        {"minimum_amount": "-0.001"}, {"minimum_amount": 123.455},
                        {"midpoint_amount": "0"}, {"minimum_amount": "400"},
                        {"maximum_amount": "150"}, {"maximum_amount": "NaN"}):
            with self.subTest(changes=changes):
                response = self.client.post("/bands", json={**self.payload, **changes}, headers=self.headers)
                self.assertEqual(response.status_code, 422, response.text)

    def test_edit_requires_current_revision_and_preserves_invalid_edits(self):
        original = self.create().json()
        self.assertEqual(self.client.put("/bands/India/L1", json=self.amounts,
                                         headers=self.headers).status_code, 428)
        headers = {**self.headers, "If-Match": original["etag"]}
        invalid = self.client.put("/bands/India/L1", json={**self.amounts, "minimum_amount": "500"}, headers=headers)
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(self.client.get("/bands/India/L1").json(), original)
        updated = self.client.put("/bands/India/L1", json={**self.amounts, "midpoint_amount": "250"}, headers=headers)
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["revision"], 2)
        self.assertNotEqual(updated.headers["etag"], original["etag"])
        self.assertEqual(updated.json()["midpoint_minor"], "25000")
        self.assertEqual(self.client.put("/bands/India/L1", json=self.amounts, headers=headers).status_code, 412)
        self.assertEqual(self.client.get("/bands/India/L1").json(), updated.json())

    def test_delete_confirm_revision_employee_preservation_and_recreate_identity(self):
        with transaction(self.path) as connection:
            insert_employee(connection, Employee("0001", "Name", "person@example.com", "Sales", "L1", "India", 99999))
        def state():
            with connect(self.path) as connection:
                employee = dict(connection.execute("SELECT * FROM employees").fetchone())
                matched = connection.execute("""SELECT b.midpoint_minor FROM employees e
                    LEFT JOIN salary_bands b ON e.country=b.country AND e.level=b.level""").fetchone()[0]
                return employee, "Unbanded" if matched is None else "Band-matched"
        before, status = state()
        self.assertEqual(status, "Unbanded")
        original = self.create().json()
        self.assertEqual(original["affected_employee_count"], 1)
        self.assertEqual(state(), (before, "Band-matched"))
        headers = {**self.headers, "If-Match": original["etag"]}
        self.assertEqual(self.client.delete("/bands/India/L1?confirmed=true", headers=self.headers).status_code, 428)
        self.assertEqual(self.client.delete("/bands/India/L1", headers=headers).status_code, 400)
        deleted = self.client.delete("/bands/India/L1?confirmed=true", headers=headers)
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json()["affected_employee_count"], 1)
        self.assertEqual(state(), (before, "Unbanded"))
        migrate(self.path)
        with TestClient(create_app(self.settings)) as restarted:
            restarted.cookies.update(self.client.cookies)
            self.assertEqual(restarted.get("/bands").json()["total"], 0)
        recreated = self.create().json()
        self.assertEqual(recreated["revision"], 1)
        self.assertNotEqual(recreated["etag"], original["etag"])
        self.assertEqual(state(), (before, "Band-matched"))
        self.assertEqual(self.client.delete("/bands/India/L1?confirmed=true", headers=headers).status_code, 412)
        self.assertEqual(self.client.put("/bands/India/L1", json=self.amounts, headers=headers).status_code, 412)

    def test_authentication_and_csrf_protect_every_band_mutation(self):
        with TestClient(create_app(self.settings)) as anonymous:
            self.assertEqual(anonymous.get("/bands").status_code, 401)
            self.assertEqual(anonymous.post("/bands", json=self.payload,
                headers={"Origin": "http://testserver"}).status_code, 401)
        for method, path, payload in (("POST", "/bands", self.payload),
                                       ("PUT", "/bands/India/L1", self.amounts),
                                       ("DELETE", "/bands/India/L1?confirmed=true", None)):
            response = self.client.request(method, path, json=payload, headers={"Origin": "http://testserver"})
            self.assertEqual(response.status_code, 403)

    def test_missing_band_returns_not_found_and_wildcard_cannot_bypass_revisions(self):
        self.assertEqual(self.client.get("/bands/US/L8").status_code, 404)
        self.create()
        self.assertEqual(self.client.put("/bands/India/L1", json=self.amounts,
            headers={**self.headers, "If-Match": "*"}).status_code, 412)

    def test_concurrent_edits_with_same_etag_allow_exactly_one_writer(self):
        etag = self.create().json()["etag"]
        cookie = self.client.cookies.get("salary_session")
        def attempt(midpoint):
            with TestClient(create_app(self.settings)) as client:
                return client.put("/bands/India/L1", json={**self.amounts, "midpoint_amount": midpoint},
                    headers={**self.headers, "If-Match": etag, "Cookie": f"salary_session={cookie}"}).status_code
        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(attempt, ("210", "220")))
        self.assertCountEqual(statuses, [200, 412])
        self.assertEqual(self.client.get("/bands/India/L1").json()["revision"], 2)
