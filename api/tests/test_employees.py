import csv
import io
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import employees
from app.config import Settings
from app.database import connect, transaction
from app.main import create_app
from app.migrate import migrate
from app.models import COUNTRY_CURRENCIES, Employee, SalaryBand
from app.normalization import normalized_key
from app.passwords import hash_password
from app.repository import insert_band, insert_employee

ROOT = "/employees"
RECORD = ROOT + "/records/"
PAYLOAD = dict(employee_id="Emp001", name="Alice", email="Alice@example.com", department="Engineering",
               country="India", level="L1", salary_amount="123.455")


class APIBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password_hash = hash_password("employee-test-password")

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "salary.db"
        migrate(self.path)
        self.settings = Settings(self.path, "hr@example.com", self.password_hash,
                                 "http://testserver", cookie_secure=False)
        self.client = self.enterContext(TestClient(create_app(self.settings)))
        result = self.client.post("/auth/login", headers={"Origin": "http://testserver"},
                                  json={"email": "hr@example.com", "password": "employee-test-password"})
        self.headers = {"Origin": "http://testserver", "X-CSRF-Token": result.json()["csrf_token"]}

    def create(self, **changes):
        response = self.client.post(ROOT, json={**PAYLOAD, **changes}, headers=self.headers)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def edit(self, current, **changes):
        body = {key: current[key] for key in ("name", "email", "department", "country", "level", "salary_amount")}
        return self.client.put(RECORD + current["employee_id"], json={**body, **changes},
                               headers={**self.headers, "If-Match": current["etag"]})


class EmployeeCRUDTests(APIBase):
    def test_create_detail_update_and_confirmed_delete(self):
        original = self.create(employee_id=" Emp001 ", name=" Alice ", department=" Engineering ")
        self.assertEqual((original["employee_id"], original["name"], original["department"]), ("Emp001", "Alice", "Engineering"))
        self.assertEqual((original["salary_minor"], original["salary_amount"], original["currency"]), ("12346", "123.46", "INR"))
        self.assertEqual((original["revision"], original["band_status"]), (1, "Unbanded"))
        detail = self.client.get(RECORD + "eMP001")
        self.assertEqual(detail.json(), original)
        self.assertEqual(detail.headers["etag"], original["etag"])
        updated = self.edit(original, name="Changed", salary_amount="0.01")
        self.assertEqual(updated.status_code, 200)
        self.assertEqual((updated.json()["name"], updated.json()["revision"], updated.json()["salary_minor"]), ("Changed", 2, "1"))
        self.assertEqual(updated.headers["etag"], updated.json()["etag"])
        headers = {**self.headers, "If-Match": updated.json()["etag"]}
        self.assertEqual(self.client.delete(RECORD + "Emp001", headers=headers).status_code, 400)
        deleted = self.client.delete(RECORD + "Emp001?confirmed=true", headers=headers)
        self.assertEqual(deleted.json(), {**updated.json(), "deleted": True})
        self.assertEqual(self.client.get(RECORD + "Emp001").status_code, 404)
        self.assertEqual(self.client.get(ROOT).json()["total"], 0)

    def test_immutable_ids_and_invalid_payloads_leave_records_unchanged(self):
        original = self.create()
        self.assertEqual(self.edit(original, employee_id="changed").status_code, 422)
        for changes in ({"name": " "}, {"name": "bad\0text"}, {"email": "invalid"}, {"department": ""},
                        {"country": "France"}, {"level": "L9"}, {"salary_amount": "-0.001"},
                        {"salary_amount": 12.3}, {"salary_amount": True}, {"salary_amount": "NaN"},
                        {"salary_minor": "100"}, {"salary_amount": "92233720368547758.08"}):
            with self.subTest(changes=changes):
                response = self.edit(original, **changes)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertIsInstance(response.json()["detail"], list)
        self.assertEqual(self.client.get(RECORD + "Emp001").json(), original)

    def test_duplicate_id_email_and_both_report_fields_case_insensitively(self):
        self.create(employee_id="Straße")
        for changes, expected in (({"employee_id": "STRASSE", "email": "other@example.com"}, {"employee_id"}),
                                  ({"employee_id": "other", "email": " ALICE@EXAMPLE.COM "}, {"email"}),
                                  ({"employee_id": "STRASSE"}, {"employee_id", "email"})):
            with self.subTest(changes=changes):
                response = self.client.post(ROOT, json={**PAYLOAD, **changes}, headers=self.headers)
                self.assertEqual(response.status_code, 409)
                detail = response.json()["detail"]
                self.assertEqual(detail["code"], "employee_conflict")
                self.assertEqual({error["field"] for error in detail["errors"]}, expected)
        self.assertEqual(self.client.get(ROOT).json()["total"], 1)

    def test_duplicate_email_edit_rolls_back_and_same_employee_case_change_is_allowed(self):
        first = self.create()
        second = self.create(employee_id="Emp002", email="second@example.com")
        duplicate = self.edit(second, email="ALICE@EXAMPLE.COM", name="Must not save")
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(self.client.get(RECORD + "Emp002").json(), second)
        changed = self.edit(first, email="ALICE@EXAMPLE.COM")
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.json()["email"], "ALICE@EXAMPLE.COM")

    def test_missing_and_stale_revision_conflicts_include_current_record(self):
        original = self.create()
        body = {key: original[key] for key in ("name", "email", "department", "country", "level", "salary_amount")}
        self.assertEqual(self.client.put(RECORD + "Emp001", json=body, headers=self.headers).status_code, 428)
        self.assertEqual(self.client.delete(RECORD + "Emp001?confirmed=true", headers=self.headers).status_code, 428)
        current = self.edit(original, name="Latest").json()
        for response in (self.edit(original, name="Stale"), self.client.delete(RECORD + "Emp001?confirmed=true",
                        headers={**self.headers, "If-Match": original["etag"]})):
            self.assertEqual(response.status_code, 412)
            self.assertEqual(response.json()["detail"]["current"], current)

    def test_concurrent_edits_allow_one_winner(self):
        original = self.create()
        def attempt(name):
            return self.edit(original, name=name)
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(attempt, ("One", "Two")))
        self.assertCountEqual([r.status_code for r in responses], [200, 412])
        winner = next(r.json() for r in responses if r.status_code == 200)
        self.assertEqual(winner["revision"], 2)
        self.assertEqual(self.client.get(RECORD + "Emp001").json(), winner)

    def test_recreated_employee_rejects_old_lifetime_token(self):
        original = self.create()
        self.client.delete(RECORD + "Emp001?confirmed=true", headers={**self.headers, "If-Match": original["etag"]})
        recreated = self.create()
        self.assertEqual(recreated["revision"], 1)
        self.assertNotEqual(original["etag"], recreated["etag"])
        self.assertEqual(self.edit(original, name="Stale").status_code, 412)

    def test_current_band_status_out_of_band_zero_and_maximum_salaries(self):
        current = self.create()
        with transaction(self.path) as connection:
            insert_band(connection, SalaryBand("India", "L1", 20000, 30000, 40000))
        self.assertEqual(self.client.get(RECORD + "Emp001").json()["band_status"], "Band-matched")
        self.assertEqual(self.client.get(ROOT).json()["items"][0]["band_status"], "Band-matched")
        for amount in ("0.00", "92233720368547758.07"):
            result = self.edit(current, salary_amount=amount)
            self.assertEqual(result.status_code, 200, result.text)
            current = result.json()
            self.assertEqual(current["band_status"], "Band-matched")
        with transaction(self.path) as connection:
            connection.execute("DELETE FROM salary_bands")
        self.assertEqual(self.client.get(RECORD + "Emp001").json()["band_status"], "Unbanded")
        self.assertEqual(self.edit(current, salary_amount="50").json()["band_status"], "Unbanded")

    def test_country_change_requires_explicit_salary_and_confirmation(self):
        original = self.create()
        response = self.edit(original, country="US", salary_amount="50000")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "country_confirmation_required")
        self.assertEqual(self.client.get(RECORD + "Emp001").json(), original)
        body = {key: original[key] for key in ("name", "email", "department", "country", "level", "salary_amount")}
        body.update(country="US", salary_amount="50000")
        response = self.client.put(RECORD + "Emp001?country_change_confirmed=true", json=body,
                                   headers={**self.headers, "If-Match": original["etag"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.json()["currency"], response.json()["salary_minor"]), ("USD", "5000000"))
        del body["salary_amount"]
        self.assertEqual(self.client.put(RECORD + "Emp001?country_change_confirmed=true", json=body,
                         headers={**self.headers, "If-Match": response.json()["etag"]}).status_code, 422)

    def test_auth_csrf_and_origin_protect_directory_and_mutations(self):
        with TestClient(create_app(self.settings)) as anonymous:
            for path in (ROOT, ROOT + "/filter-options", ROOT + "/export.csv", RECORD + "missing"):
                self.assertEqual(anonymous.get(path).status_code, 401)
            self.assertEqual(anonymous.post(ROOT, json=PAYLOAD, headers={"Origin": "http://testserver"}).status_code, 401)
        self.assertEqual(self.client.post(ROOT, json=PAYLOAD, headers={"Origin": "http://testserver"}).status_code, 403)
        self.assertEqual(self.client.post(ROOT, json=PAYLOAD, headers={**self.headers, "Origin": "https://other.example"}).status_code, 403)
        current = self.create()
        self.assertEqual(self.client.put(RECORD + "Emp001", json=PAYLOAD, headers={"Origin": "http://testserver"}).status_code, 403)
        self.assertEqual(self.client.delete(RECORD + "Emp001?confirmed=true", headers={"Origin": "http://testserver"}).status_code, 403)
        self.assertEqual(self.client.get(RECORD + "Emp001").json(), current)

    def test_arbitrary_ids_have_no_route_collision(self):
        for index, identity in enumerate(("filter-options", "export.csv", "a/b", "编号 1")):
            result = self.create(employee_id=identity, email=f"person{index}@example.com")
            self.assertEqual(self.client.get(RECORD + identity).json(), result)

    def test_migration_backfills_employee_lifetimes_and_restart_preserves_tokens(self):
        original = self.create()
        with transaction(self.path) as connection:
            connection.execute("DROP TRIGGER employees_identity")
            connection.execute("DROP TABLE employee_identities")
            for table in ("import_receipts", "import_preview_context", "import_preview_errors", "import_preview_rows", "import_previews"):
                connection.execute(f"DROP TABLE {table}")
            connection.execute("PRAGMA user_version=3")
        migrate(self.path)
        migrated = self.client.get(RECORD + "Emp001").json()
        self.assertEqual({k: v for k, v in migrated.items() if k != "etag"},
                         {k: v for k, v in original.items() if k != "etag"})
        migrate(self.path)
        with TestClient(create_app(self.settings)) as restarted:
            restarted.cookies.update(self.client.cookies)
            self.assertEqual(restarted.get(RECORD + "Emp001").json(), migrated)


class DirectoryTests(APIBase):
    def setUp(self):
        super().setUp()
        self.rows = []
        # Deliberately insert IDs in reverse order; every sortable non-ID field has ties.
        with transaction(self.path) as connection:
            for number in reversed(range(113)):
                country = list(COUNTRY_CURRENCIES)[number % 6]
                employee = Employee(f"Emp{number:04}", f"Name {number % 7}", f"person{number:04}@example.com",
                                    ("Engineering", "Sales", "engineering")[number % 3],
                                    f"L{number % 8 + 1}", country, (number % 5) * 101)
                insert_employee(connection, employee)
                self.rows.append(employee)
            insert_band(connection, SalaryBand("India", "L1", 0, 100, 200))

    def ids(self, params=None):
        response = self.client.get(ROOT, params=params or {})
        self.assertEqual(response.status_code, 200, response.text)
        return [item["employee_id"] for item in response.json()["items"]]

    def expected(self, sort, direction):
        def key(employee):
            if sort == "salary":
                return employee.salary_minor
            return normalized_key(getattr(employee, sort))
        # Independent stable sort oracle: ID ascending breaks all ties in either direction.
        result = sorted(self.rows, key=lambda employee: normalized_key(employee.employee_id))
        result.sort(key=key, reverse=direction == "desc")
        if sort == "salary":
            result.sort(key=lambda employee: employee.country)
        return [employee.employee_id for employee in result]

    def test_every_sort_direction_level_order_salary_country_group_and_stable_ties(self):
        for sort in ("employee_id", "name", "email", "department", "country", "level", "salary"):
            for direction in ("asc", "desc"):
                with self.subTest(sort=sort, direction=direction):
                    params = dict(sort=sort, direction=direction, page_size=100)
                    actual = self.ids(params) + self.ids({**params, "page": 2})
                    self.assertEqual(actual, self.expected(sort, direction))
                    self.assertEqual(self.ids(params), actual[:100])
        self.assertIn("Country ascending", self.client.get(ROOT, params={"sort": "salary"}).json()["ordering"])

    def test_search_all_fields_trim_casefold_and_combined_filters(self):
        for search, expected in ((" EMP0003 ", ["Emp0003"]), (" PERSON0004@EXAMPLE.COM ", ["Emp0004"]),
                                  (" name 2 ", sorted(e.employee_id for e in self.rows if e.name == "Name 2"))):
            with self.subTest(search=search):
                self.assertEqual(self.ids({"search": search, "page_size": 100}), expected)
        params = dict(search="NAME", country=" India ", level=" L1 ", department=" ENGINEERING ", page_size=100)
        expected = sorted(e.employee_id for e in self.rows if e.country == "India" and e.level == "L1"
                          and normalized_key(e.department) == "engineering")
        self.assertTrue(expected)
        self.assertEqual(self.ids(params), expected)
        self.assertEqual(self.client.get(ROOT, params=params).json()["total"], len(expected))
        self.assertEqual(len(self.ids({"search": "   ", "page_size": 100})), 100)

    def test_literal_wildcards_quotes_unicode_and_sql_injection_are_safe(self):
        with transaction(self.path) as connection:
            insert_employee(connection, Employee("special", "Straße 100%_\\'", "special@example.com", "Sales", "L1", "US", 0))
        for query in ("STRASSE", "%", "_", "\\", "'"):
            self.assertEqual(self.ids({"search": query}), ["special"])
        self.assertEqual(self.ids({"search": "' OR 1=1 --"}), [])

    def test_pagination_defaults_all_sizes_totals_last_and_out_of_range(self):
        all_ids = sorted(e.employee_id for e in self.rows)
        default = self.client.get(ROOT).json()
        self.assertEqual((default["page"], default["page_size"], default["total"], default["total_pages"]), (1, 25, 113, 5))
        for size in (25, 50, 100):
            pages = (113 + size - 1) // size
            collected = []
            for page in range(1, pages + 1):
                response = self.client.get(ROOT, params=dict(page=page, page_size=size)).json()
                self.assertEqual((response["total"], response["total_pages"]), (113, pages))
                self.assertEqual(response["items"], self.client.get(ROOT, params=dict(page=page, page_size=size)).json()["items"])
                collected += [row["employee_id"] for row in response["items"]]
            self.assertEqual(collected, all_ids)
            response = self.client.get(ROOT, params=dict(page=pages + 1, page_size=size)).json()
            self.assertEqual((response["items"], response["total"]), ([], 113))
        self.assertEqual(self.client.get(ROOT, params={"page": "9" * 100}).json()["items"], [])

    def test_allowlists_and_invalid_pagination_are_validation_errors(self):
        for params in ({"sort": "salary_minor"}, {"sort": "name; DROP TABLE employees"}, {"direction": "sideways"},
                       {"page_size": 10}, {"page": 0}, {"page": -1}, {"page": "1.5"},
                       {"country": "France"}, {"level": "L9"}, {"unknown": "ignored?"}):
            with self.subTest(params=params):
                response = self.client.get(ROOT, params=params)
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.client.get(ROOT).json()["total"], 113)

    def test_empty_results_and_blank_filters(self):
        response = self.client.get(ROOT, params={"search": "missing"}).json()
        self.assertEqual((response["items"], response["total"], response["total_pages"]), ([], 0, 0))
        self.assertEqual(self.client.get(ROOT, params={"country": " ", "level": "", "department": " "}).json()["total"], 113)

    def test_filter_options_group_departments_without_case_duplicates(self):
        options = self.client.get(ROOT + "/filter-options").json()
        self.assertEqual(set(options["countries"]), set(COUNTRY_CURRENCIES))
        self.assertEqual(options["levels"], [f"L{i}" for i in range(1, 9)])
        self.assertEqual(options["departments"], [{"value": "engineering", "label": "Engineering"}, {"value": "sales", "label": "Sales"}])

    def test_csv_full_filtered_parity_every_sort_direction_ignores_page(self):
        for sort in ("employee_id", "name", "email", "department", "country", "level", "salary"):
            for direction in ("asc", "desc"):
                with self.subTest(sort=sort, direction=direction):
                    params = dict(sort=sort, direction=direction, department="ENGINEERING", search="person")
                    directory = self.client.get(ROOT, params={**params, "page_size": 100}).json()
                    response = self.client.get(ROOT + "/export.csv", params={**params, "page_size": 25, "page": 99})
                    self.assertEqual(response.status_code, 200)
                    self.assertIn("text/csv", response.headers["content-type"])
                    self.assertIn("attachment", response.headers["content-disposition"])
                    rows = list(csv.reader(io.StringIO(response.text)))
                    self.assertEqual(rows[0], list(employees.CSV_HEADER))
                    self.assertEqual([row[0] for row in rows[1:]], [item["employee_id"] for item in directory["items"]])
                    self.assertEqual(len(rows) - 1, directory["total"])
                    for row, item in zip(rows[1:], directory["items"]):
                        self.assertEqual(row[6:], [item["currency"], item["salary_amount"], item["band_status"]])
        params = dict(country="India", level="L1", department="engineering", search="name", sort="salary", direction="desc")
        rows = list(csv.reader(io.StringIO(self.client.get(ROOT + "/export.csv", params=params).text)))
        self.assertEqual([r[0] for r in rows[1:]], self.ids(params))

    def test_header_only_empty_export(self):
        response = self.client.get(ROOT + "/export.csv", params={"search": "missing"})
        self.assertEqual(list(csv.reader(io.StringIO(response.text))), [list(employees.CSV_HEADER)])

    def test_csv_unicode_quotes_commas_newlines_formulas_and_exact_money(self):
        values = ["=1+1", "+SUM(A1)", "-2+3", "@SUM(A1)", 'Zoë, "नमस्ते"\n第二行', "\t=cmd", "\r=cmd", "\n=cmd", "  =cmd"]
        with transaction(self.path) as connection:
            for index, name in enumerate(values):
                insert_employee(connection, Employee(f"=id{index}", name, f"+formula{index}@example.com",
                                    "@Finance", "L1", "US", 12346 if index else 9223372036854775807))
        rows = list(csv.reader(io.StringIO(self.client.get(ROOT + "/export.csv", params={"search": "=id"}).text)))
        self.assertEqual(len(rows), len(values) + 1)
        for index, row in enumerate(rows[1:]):
            self.assertEqual(row[0], f"'=id{index}")
            self.assertEqual(row[1], "'" + values[index].strip() if index != 4 else values[index])
            self.assertEqual(row[2], f"'+formula{index}@example.com")
            self.assertEqual(row[3], "'@Finance")
            self.assertEqual(row[7], "123.46" if index else "92233720368547758.07")
        for prefix in ("\t", "\r", "\n", "   "):
            self.assertEqual(employees.safe_csv_text(prefix + "=1"), "'" + prefix + "=1")

    def test_count_and_page_use_same_snapshot_with_intervening_write(self):
        original_count = employees.count_employees
        def count_then_write(connection, query):
            count = original_count(connection, query)
            with transaction(self.path) as writer:
                insert_employee(writer, Employee("AAA", "New", "new@example.com", "Sales", "L1", "India", 0))
            return count
        with patch("app.employees.count_employees", side_effect=count_then_write):
            response = self.client.get(ROOT).json()
        self.assertEqual(response["total"], 113)
        self.assertEqual(response["items"][0]["employee_id"], "Emp0000")
        self.assertEqual(self.client.get(ROOT).json()["total"], 114)

    def test_csv_snapshot_survives_band_and_employee_changes_mid_export(self):
        original_safe = employees.safe_csv_text
        changed = False
        def change_during_export(value):
            nonlocal changed
            if not changed:
                changed = True
                with transaction(self.path) as writer:
                    writer.execute("DELETE FROM salary_bands")
                    writer.execute("UPDATE employees SET name='Updated', salary_minor=999, revision=revision+1")
                    insert_employee(writer, Employee("New", "New", "new@example.com", "Sales", "L1", "India", 0))
            return original_safe(value)
        with patch("app.employees.safe_csv_text", side_effect=change_during_export):
            response = self.client.get(ROOT + "/export.csv")
        rows = list(csv.DictReader(io.StringIO(response.text)))
        self.assertEqual(len(rows), 113)
        self.assertFalse(any(row["Name"] == "Updated" for row in rows))
        self.assertTrue(any(row["Band Status"] == "Band-matched" for row in rows))
        self.assertEqual(self.client.get(ROOT).json()["total"], 114)
        self.assertTrue(all(row["band_status"] == "Unbanded" for row in self.client.get(ROOT).json()["items"]))

    def test_csv_storage_failure_does_not_return_successful_truncated_download(self):
        with patch("app.employees.format_money", side_effect=RuntimeError("simulated export failure")):
            with TestClient(create_app(self.settings), raise_server_exceptions=False) as client:
                client.cookies.update(self.client.cookies)
                response = client.get(ROOT + "/export.csv")
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("content-disposition", response.headers)


class LargeDirectoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        cls.path = Path(cls.directory.name) / "salary.db"
        migrate(cls.path)
        # Deterministic API-owned domain/repository setup, separate from workbook import.
        with transaction(cls.path) as connection:
            for number in reversed(range(10000)):
                insert_employee(connection, Employee(f"EMP{number:05}", f"Employee {number:05}",
                    f"person{number:05}@example.com", "Engineering" if number % 2 else "Sales",
                    f"L{number % 8 + 1}", list(COUNTRY_CURRENCIES)[number % 6], number * 101))
        settings = Settings(cls.path, "hr@example.com", hash_password("large-fixture-password"),
                            "http://testserver", cookie_secure=False)
        cls.client = cls.enterClassContext(TestClient(create_app(settings)))
        cls.client.post("/auth/login", headers={"Origin": "http://testserver"},
                        json={"email": "hr@example.com", "password": "large-fixture-password"})

    def test_10000_fixture_returns_only_requested_page_all_sizes(self):
        for size in (25, 50, 100):
            with self.subTest(size=size):
                result = self.client.get(ROOT, params={"page_size": size, "page": 7}).json()
                self.assertEqual(result["total"], 10000)
                self.assertEqual(len(result["items"]), size)
                self.assertEqual([row["employee_id"] for row in result["items"]],
                                 [f"EMP{number:05}" for number in range(6 * size, 7 * size)])
        result = self.client.get(ROOT).json()
        self.assertEqual((len(result["items"]), result["total"]), (25, 10000))

    def test_10000_filtered_population_count_and_page(self):
        result = self.client.get(ROOT, params={"country": "US", "level": "L2", "department": "Engineering", "page_size": 50}).json()
        expected = [n for n in range(10000) if n % 6 == 1 and n % 8 == 1 and n % 2]
        self.assertEqual(result["total"], len(expected))
        self.assertEqual([row["employee_id"] for row in result["items"]], [f"EMP{n:05}" for n in expected[:50]])

    def test_10000_export_includes_entire_population_in_directory_order(self):
        response = self.client.get(ROOT + "/export.csv", params={"direction": "desc", "page": 3})
        rows = list(csv.reader(io.StringIO(response.text)))
        self.assertEqual(len(rows), 10001)
        self.assertEqual([row[0] for row in rows[1:]], [f"EMP{n:05}" for n in reversed(range(10000))])
