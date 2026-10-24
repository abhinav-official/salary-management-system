import os
import random
import tempfile
import unittest
from collections import Counter, defaultdict
from contextlib import contextmanager
from decimal import FloatOperation, ROUND_DOWN, localcontext
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from fastapi.testclient import TestClient

from app import equity
from app.analytics import PopulationFilters
from app.config import Settings
from app.database import read_transaction, transaction
from app.equity import PeerGroup
from app.load_bands import load_bands
from app.main import create_app
from app.migrate import migrate
from app.models import COUNTRY_CURRENCIES, Employee, LEVELS, SalaryBand
from app.money import MAX_MINOR, parse_money
from app.passwords import hash_password
from app.repository import insert_band, insert_employee

COUNTS = ("available_count", "excluded_unbanded_count", "insufficient_peers_count", "zero_peer_median_count")
SAMPLE = Path(os.environ.get("BAND_SAMPLE_WORKBOOK", Path(__file__).parents[2] / "data/employees_10000.xlsx"))


def reference_median(values):
    # Deliberately copy and sort the remaining peers anew for each target.
    values = sorted(values)
    count = len(values)
    if not count:
        return None
    if count % 2:
        return Fraction(values[count // 2])
    return Fraction(values[count // 2 - 1] + values[count // 2], 2)


def reference_display(value):
    # Independent integer half-up rounding; no production statistic/Decimal helper.
    cents, remainder = divmod(abs(value.numerator) * 100, value.denominator)
    if remainder * 2 >= value.denominator:
        cents += 1
    return ("-" if value < 0 else "") + f"{cents // 100}.{cents % 100:02d}"


def reference_comparison(target, group, band_keys):
    if (target.country, target.level) not in band_keys:
        return "unbanded", 0, None, None
    peers = [other.salary_minor for other in group if other.employee_id_key != target.employee_id_key]
    median = reference_median(peers)
    if median is None:
        return "insufficient_peers", 0, None, None
    if median == 0:
        return "zero_peer_median", len(peers), median, None
    difference = Fraction(target.salary_minor * median.denominator - median.numerator, median.numerator) * 100
    return "available", len(peers), median, reference_display(difference)


def assert_reference(test, result, targets, population, band_keys):
    groups = defaultdict(list)
    for employee in population:
        if (employee.country, employee.level) in band_keys:
            groups[employee.country, employee.level].append(employee)
    expected_counts = Counter()
    actual = {item["employee_id"]: item for item in result["items"]}
    test.assertEqual(set(actual), {employee.employee_id for employee in targets})
    count_keys = {"available": "available_count", "unbanded": "excluded_unbanded_count",
                  "insufficient_peers": "insufficient_peers_count", "zero_peer_median": "zero_peer_median_count"}
    for target in targets:
        expected = reference_comparison(target, groups[target.country, target.level], band_keys)
        state, peer_count, median, difference = expected
        item = actual[target.employee_id]
        test.assertEqual((item["state"], item["peer_count"], item["percentage_difference"]), (state, peer_count, difference))
        if median is None:
            test.assertIsNone(item["peer_median"])
        else:
            test.assertEqual(item["peer_median"], {"minor_numerator": str(median.numerator),
                "minor_denominator": str(median.denominator), "amount": reference_display(median / 100)})
        test.assertEqual(item["salary_minor"], str(target.salary_minor))
        test.assertEqual(item["currency"], COUNTRY_CURRENCIES[target.country])
        expected_counts[count_keys[state]] += 1
    for key in COUNTS:
        test.assertEqual(result[key], expected_counts[key])
    test.assertEqual(result["total_count"], len(targets))
    test.assertEqual(sum(result[key] for key in COUNTS), len(targets))


class PeerGroupTests(unittest.TestCase):
    def test_every_exclusion_position_odd_even_ties_and_money_bounds(self):
        cases = ([0], [100, 200], [0, 1, 1], [100, 100, 100, 100], [0, 0, 1, 1, 2],
                 [0, 1, 2, 3, 4, 5], [0, MAX_MINOR, MAX_MINOR])
        for values in cases:
            members = [(f"id{index}", value) for index, value in enumerate(values)]
            group = PeerGroup(reversed(members))
            self.assertEqual(group.peer_count, len(members) - 1)
            for identity, _ in members:
                expected = reference_median(value for other, value in members if other != identity)
                self.assertEqual(group.median_without(identity), expected)

    def test_fixed_seed_optimized_medians_match_reference_at_every_position(self):
        rng = random.Random(42)
        for count in (1, 2, 3, 4, 5, 10, 31, 32, 100):
            for _ in range(10):
                members = [(f"id{index:03}", rng.randrange(20)) for index in range(count)]
                rng.shuffle(members)
                group = PeerGroup(members)
                for identity, _ in members:
                    self.assertEqual(group.median_without(identity), reference_median(
                        value for other, value in members if other != identity))

    def test_duplicate_identities_and_invalid_money_are_rejected(self):
        with self.assertRaises(ValueError):
            PeerGroup([])
        with self.assertRaises(ValueError):
            PeerGroup([("same", 1), ("same", 2)])
        for value in (1.0, True, -1, MAX_MINOR + 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                PeerGroup([("id", value)])


class EquityAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password_hash = hash_password("equity-test-password")

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "salary.db"
        migrate(self.path)
        self.settings = Settings(self.path, "hr@example.com", self.password_hash, "http://testserver", cookie_secure=False)
        self.client = self.enterContext(TestClient(create_app(self.settings)))
        login = self.client.post("/auth/login", headers={"Origin": "http://testserver"},
            json={"email": "hr@example.com", "password": "equity-test-password"})
        self.headers = {"Origin": "http://testserver", "X-CSRF-Token": login.json()["csrf_token"]}
        self.sequence = 0

    def employee(self, salary, *, country="India", level="L1", department="Engineering", identity=None):
        self.sequence += 1
        result = Employee(identity or f"EMP{self.sequence:05}", f"Person {self.sequence}",
                          f"person{self.sequence}@example.com", department, level, country, salary)
        with transaction(self.path) as connection:
            insert_employee(connection, result)
        return result

    def band(self, country="India", level="L1"):
        with transaction(self.path) as connection:
            insert_band(connection, SalaryBand(country, level, 0, 10000, 20000))

    def result(self, **params):
        response = self.client.get("/analytics/equity", params=params)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["total_count"], len(result["items"]))
        self.assertEqual(sum(result[key] for key in COUNTS), result["total_count"])
        self.assertEqual(result["peer_scope"], "full_saved_country_level_excluding_self")
        return result

    def test_empty_and_singleton_insufficient_peers(self):
        empty = self.result()
        self.assertEqual(empty["items"], [])
        self.assertTrue(all(empty[key] == 0 for key in COUNTS))
        self.band()
        self.employee(10000)
        result = self.result()
        self.assertEqual(result["insufficient_peers_count"], 1)
        item = result["items"][0]
        self.assertEqual((item["state"], item["state_label"], item["peer_count"]), ("insufficient_peers", "Insufficient peers", 0))
        self.assertIsNone(item["peer_median"])
        self.assertIsNone(item["percentage_difference"])

    def test_two_people_have_one_other_peer_and_valid_comparisons(self):
        self.band()
        employees = [self.employee(salary) for salary in (10000, 20000)]
        result = self.result()
        self.assertEqual(result["available_count"], 2)
        self.assertEqual([item["peer_count"] for item in result["items"]], [1, 1])
        self.assertEqual([item["percentage_difference"] for item in result["items"]], ["-50.00", "100.00"])
        assert_reference(self, result, employees, employees, {("India", "L1")})

    def test_requirements_100_200_400_example_all_three_positions(self):
        self.band()
        employees = [self.employee(salary) for salary in (10000, 20000, 40000)]
        result = self.result()
        self.assertEqual([item["peer_median"]["amount"] for item in result["items"]], ["300.00", "250.00", "150.00"])
        self.assertEqual([item["percentage_difference"] for item in result["items"]], ["-66.67", "-20.00", "166.67"])
        assert_reference(self, result, employees, employees, {("India", "L1")})

    def test_even_odd_peer_counts_and_tied_salaries_remove_one_identity_only(self):
        self.band()
        for count, values in ((4, [100, 100, 200, 400]), (5, [100, 100, 100, 200, 400])):
            with transaction(self.path) as connection:
                connection.execute("DELETE FROM employees")
            employees = [self.employee(value) for value in values]
            result = self.result()
            self.assertTrue(all(item["peer_count"] == count - 1 for item in result["items"]))
            assert_reference(self, result, employees, employees, {("India", "L1")})

    def test_fractional_peer_median_is_not_rounded_before_percentage(self):
        self.band()
        employees = [self.employee(value) for value in (1, 0, 1)]
        result = self.result()
        item = result["items"][0]
        self.assertEqual(item["peer_median"], {"minor_numerator": "1", "minor_denominator": "2", "amount": "0.01"})
        self.assertEqual(item["percentage_difference"], "100.00")
        assert_reference(self, result, employees, employees, {("India", "L1")})

    def test_half_up_negative_positive_and_exact_zero_percentage(self):
        self.band()
        self.band(level="L2")
        self.band(level="L3")
        employees = [self.employee(value, level=level) for level, values in
                     (("L1", (31, 32, 32)), ("L2", (33, 32, 32)), ("L3", (32, 32))) for value in values]
        result = self.result()
        self.assertEqual(result["items"][0]["percentage_difference"], "-3.13")
        self.assertEqual(result["items"][3]["percentage_difference"], "3.13")
        self.assertEqual(result["items"][6]["percentage_difference"], "0.00")
        assert_reference(self, result, employees, employees, {("India", level) for level in ("L1", "L2", "L3")})

    def test_zero_median_preserves_real_peer_count_and_median_and_never_divides(self):
        self.band()
        employees = [self.employee(value) for value in (0, 0, 10000)]
        result = self.result()
        self.assertEqual((result["available_count"], result["zero_peer_median_count"]), (2, 1))
        item = result["items"][2]
        self.assertEqual((item["state"], item["state_label"], item["peer_count"]), ("zero_peer_median", "Peer median is zero", 2))
        self.assertEqual(item["peer_median"], {"minor_numerator": "0", "minor_denominator": "1", "amount": "0.00"})
        self.assertIsNone(item["percentage_difference"])
        assert_reference(self, result, employees, employees, {("India", "L1")})

    def test_all_four_mutually_exclusive_states_reconcile_and_currencies_never_mix(self):
        self.band()
        self.band("US", "L2")
        self.band("UK", "L3")
        employees = [self.employee(10000), self.employee(20000), self.employee(10000, country="US", level="L2"),
                     self.employee(0, country="UK", level="L3"), self.employee(0, country="UK", level="L3"),
                     self.employee(50000, country="Germany", level="L4")]
        result = self.result()
        self.assertEqual([result[key] for key in COUNTS], [2, 1, 1, 2])
        unbanded = result["items"][-1]
        self.assertEqual((unbanded["band_status"], unbanded["state_label"], unbanded["peer_count"]), ("Unbanded", "Unbanded", 0))
        self.assertIsNone(unbanded["peer_median"])
        self.assertIsNone(unbanded["percentage_difference"])
        assert_reference(self, result, employees, employees, {("India", "L1"), ("US", "L2"), ("UK", "L3")})

    def test_all_unbanded_are_present_but_excluded(self):
        employees = [self.employee(salary) for salary in (0, 100, MAX_MINOR)]
        result = self.result()
        self.assertEqual([result[key] for key in COUNTS], [0, 3, 0, 0])
        assert_reference(self, result, employees, employees, set())

    def test_department_filter_changes_only_targets_never_country_level_peers(self):
        self.band()
        employees = [self.employee(10000, department="Straße"), self.employee(20000, department="Sales"),
                     self.employee(40000, department="Sales")]
        filtered = self.result(country=" India ", level=" L1 ", department=" STRASSE ")
        self.assertEqual(filtered["filters"], {"country": "India", "level": "L1", "department": "strasse"})
        self.assertEqual((filtered["total_count"], filtered["available_count"]), (1, 1))
        self.assertEqual(filtered["items"][0]["peer_count"], 2)
        self.assertEqual(filtered["items"][0]["peer_median"]["amount"], "300.00")
        self.assertEqual(filtered["items"][0], self.result()["items"][0])
        assert_reference(self, filtered, employees[:1], employees, {("India", "L1")})
        self.assertEqual(self.result(department="Missing")["items"], [])
        self.assertEqual(self.result(department=" ", country="", level=" ")["total_count"], 3)

    def test_country_and_level_isolate_peers_with_same_salary_and_department(self):
        keys = {(country, level) for country, level in (("India", "L1"), ("US", "L1"), ("India", "L2"))}
        for key in keys:
            self.band(*key)
        employees = [self.employee(salary, country=country, level=level) for country, level, salary in
                     (("India", "L1", 100), ("India", "L1", 200), ("US", "L1", 999999), ("India", "L2", 888888))]
        assert_reference(self, self.result(), employees, employees, keys)
        selected = self.result(country="India", level="L1")
        self.assertEqual([item["peer_count"] for item in selected["items"]], [1, 1])

    def test_auth_validation_no_store_response_schema_and_stable_id_order(self):
        with TestClient(create_app(self.settings)) as anonymous:
            self.assertEqual(anonymous.get("/analytics/equity").status_code, 401)
        for params in ({"page": 1}, {"page_size": 25}, {"search": "x"}, {"sort": "salary"},
                       {"country": "France"}, {"level": "L9"}, {"unknown": "x"}):
            response = self.client.get("/analytics/equity", params=params)
            self.assertEqual(response.status_code, 422, response.text)
        self.employee(100, identity="z-last")
        self.employee(200, identity="A-first")
        response = self.client.get("/analytics/equity")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual([item["employee_id"] for item in response.json()["items"]], ["A-first", "z-last"])
        self.assertIn("$ref", self.client.app.openapi()["paths"]["/analytics/equity"]["get"]["responses"]["200"]["content"]["application/json"]["schema"])

    def test_queries_and_sorts_are_bounded_by_peer_groups_not_target_count(self):
        for level in ("L1", "L2"):
            self.band(level=level)
        for number in range(60):
            self.employee(number, level="L1" if number % 2 else "L2")
        queries = []
        @contextmanager
        def traced(path):
            with read_transaction(path) as connection:
                connection.set_trace_callback(queries.append)
                yield connection
        with patch("app.equity.read_transaction", traced), patch("app.equity.PeerGroup", wraps=PeerGroup) as grouping:
            result = self.result()
        self.assertEqual(result["total_count"], 60)
        self.assertEqual(len([query for query in queries if query.lstrip().upper().startswith("SELECT")]), 2)
        self.assertEqual(grouping.call_count, 2)
        self.assertTrue(all(item["peer_count"] == 29 for item in result["items"]))

    def test_snapshot_keeps_targets_bands_and_peers_consistent_during_write(self):
        self.band()
        self.employee(100)
        self.employee(200)
        before = self.result()
        original = equity.read_targets
        def read_then_write(connection, filters):
            targets = original(connection, filters)
            with transaction(self.path) as writer:
                writer.execute("DELETE FROM salary_bands")
                writer.execute("UPDATE employees SET salary_minor=999, revision=revision+1")
            return targets
        with patch("app.equity.read_targets", side_effect=read_then_write):
            self.assertEqual(self.result(), before)
        after = self.result()
        self.assertEqual(after["excluded_unbanded_count"], 2)
        self.assertTrue(all(item["salary_minor"] == "999" for item in after["items"]))

    def test_employee_create_edit_move_delete_and_band_changes_recompute(self):
        first = self.employee(100)
        self.band()
        self.assertEqual(self.result()["insufficient_peers_count"], 1)
        second = self.employee(200)
        self.assertEqual(self.result()["available_count"], 2)
        current = self.client.get(f"/employees/records/{second.employee_id}").json()
        body = {field: current[field] for field in ("name", "email", "department", "country", "level", "salary_amount")}
        edited = self.client.put(f"/employees/records/{second.employee_id}", headers={**self.headers, "If-Match": current["etag"]}, json={**body, "salary_amount": "4.00"})
        self.assertEqual(edited.status_code, 200)
        self.assertEqual(self.result()["items"][0]["percentage_difference"], "-75.00")
        with transaction(self.path) as connection:
            connection.execute("UPDATE employees SET level='L2', revision=revision+1 WHERE employee_id=?", (second.employee_id,))
        self.assertEqual([self.result()[key] for key in COUNTS], [0, 1, 1, 0])
        self.band(level="L2")
        self.assertEqual(self.result()["insufficient_peers_count"], 2)
        with transaction(self.path) as connection:
            connection.execute("DELETE FROM salary_bands")
        self.assertEqual(self.result()["excluded_unbanded_count"], 2)
        self.band()
        self.assertEqual([self.result()[key] for key in COUNTS], [0, 1, 1, 0])
        current = self.client.get(f"/employees/records/{first.employee_id}").json()
        self.assertEqual(self.client.delete(f"/employees/records/{first.employee_id}?confirmed=true",
            headers={**self.headers, "If-Match": current["etag"]}).status_code, 200)
        self.assertEqual((self.result()["total_count"], self.result()["excluded_unbanded_count"]), (1, 1))

    def test_maximum_amounts_and_small_hostile_decimal_context(self):
        self.band()
        employees = [self.employee(value) for value in (1, 0, MAX_MINOR)]
        with localcontext() as context:
            context.prec = 2
            context.rounding = ROUND_DOWN
            context.traps[FloatOperation] = True
            # Direct domain call keeps the intentionally hostile context in this thread.
            result = equity.comparisons(self.path, PopulationFilters())
        assert_reference(self, result, employees, employees, {("India", "L1")})
        self.assertEqual(result["items"][-1]["percentage_difference"], "1844674407370955161300.00")


def sample_employees():
    """Read this committed fixture as exact text; this is not an import endpoint."""
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with ZipFile(SAMPLE) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        if workbook.find(f"{ns}sheets/{ns}sheet").get("name") != "Employees":
            raise AssertionError("Expected the committed Employees fixture as the first worksheet")
        strings = ["".join(item.itertext()) for item in ET.fromstring(archive.read("xl/sharedStrings.xml"))]
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        rows = []
        for row in sheet.findall(f"{ns}sheetData/{ns}row"):
            values = []
            for cell in row.findall(f"{ns}c"):
                if cell.get("t") == "s":
                    values.append(strings[int(cell.findtext(f"{ns}v"))])
                elif cell.get("t") == "inlineStr":
                    values.append("".join(node.text or "" for node in cell.findall(f"{ns}is//{ns}t")))
                else:
                    raise AssertionError("The committed fixture must store exact text, including money")
            rows.append(values)
    expected = ["Employee ID", "Name", "Email", "Department", "Level", "Country", "Annual Salary"]
    if rows[0] != expected:
        raise AssertionError("Unexpected fixture headers")
    return [Employee(*row[:6], parse_money(row[6])) for row in rows[1:]]


class SampleEquityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        cls.path = Path(cls.directory.name) / "salary.db"
        migrate(cls.path)
        load_bands(cls.path, SAMPLE)
        cls.employees = sample_employees()
        if len(cls.employees) != 10000:
            raise AssertionError("Expected exactly 10,000 sample employees")
        with transaction(cls.path) as connection:
            for employee in cls.employees:
                insert_employee(connection, employee)
        settings = Settings(cls.path, "hr@example.com", hash_password("sample-equity-password"), "http://testserver", cookie_secure=False)
        cls.client = cls.enterClassContext(TestClient(create_app(settings)))
        cls.client.post("/auth/login", headers={"Origin": "http://testserver"},
                        json={"email": "hr@example.com", "password": "sample-equity-password"})
        cls.band_keys = {(country, level) for country in COUNTRY_CURRENCIES for level in LEVELS}

    def test_all_10000_workbook_rows_match_independent_per_employee_reference(self):
        response = self.client.get("/analytics/equity")
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["available_count"], 10000)
        self.assertEqual([result[key] for key in COUNTS], [10000, 0, 0, 0])
        assert_reference(self, result, self.employees, self.employees, self.band_keys)
        self.assertEqual([item["employee_id"] for item in result["items"]],
                         [employee.employee_id for employee in sorted(self.employees, key=lambda employee: employee.employee_id_key)])

    def test_department_filtered_targets_still_match_full_10000_reference(self):
        targets = [employee for employee in self.employees if employee.department_key == "engineering"]
        result = self.client.get("/analytics/equity", params={"department": " ENGINEERING "}).json()
        self.assertGreater(len(targets), 100)
        self.assertEqual(result["total_count"], len(targets))
        assert_reference(self, result, targets, self.employees, self.band_keys)

    def test_10000_targets_execute_two_queries_and_sort_each_of_48_groups_once(self):
        queries = []
        @contextmanager
        def traced(path):
            with read_transaction(path) as connection:
                connection.set_trace_callback(queries.append)
                yield connection
        with patch("app.equity.read_transaction", traced), patch("app.equity.PeerGroup", wraps=PeerGroup) as grouping:
            result = self.client.get("/analytics/equity").json()
        self.assertEqual(result["total_count"], 10000)
        self.assertEqual(grouping.call_count, 48)
        self.assertEqual(len([query for query in queries if query.lstrip().upper().startswith("SELECT")]), 2)
