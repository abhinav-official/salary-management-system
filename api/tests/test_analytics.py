import random
import tempfile
import unittest
from decimal import FloatOperation, ROUND_DOWN, localcontext
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import analytics
from app.analytics import PopulationFilters
from app.config import Settings
from app.database import transaction
from app.exact_statistics import compa_bucket, format_fraction, percentage, salary_summary
from app.main import create_app
from app.migrate import migrate
from app.models import COUNTRY_CURRENCIES, Employee, SalaryBand
from app.money import MAX_MINOR
from app.passwords import hash_password
from app.repository import insert_band, insert_employee

STATISTICS = ("average", "median", "minimum", "maximum", "spread")


def exact(statistic):
    return Fraction(int(statistic["minor_numerator"]), int(statistic["minor_denominator"]))


def reference_display(value):
    # Independent integer divmod half-up oracle in minor units, without Decimal.
    whole, remainder = divmod(value.numerator, value.denominator)
    minor = whole + (1 if remainder * 2 >= value.denominator else 0)
    return f"{minor // 100}.{minor % 100:02d}"


class ExactStatisticsTests(unittest.TestCase):
    def test_odd_median_mean_extrema_and_spread(self):
        result = salary_summary([30000, 10000, 20000])
        self.assertEqual(result["count"], 3)
        expected = {"average": 20000, "median": 20000, "minimum": 10000, "maximum": 30000, "spread": 20000}
        for name, minor in expected.items():
            self.assertEqual(exact(result[name]), minor)
            self.assertEqual(result[name]["amount"], reference_display(Fraction(minor)))

    def test_even_median_retains_half_cent_until_display(self):
        result = salary_summary([10001, 10000])
        for name in ("average", "median"):
            self.assertEqual(result[name], {"minor_numerator": "20001", "minor_denominator": "2", "amount": "100.01"})
        self.assertEqual(result["spread"]["amount"], "0.01")
        self.assertEqual(exact(salary_summary([101, 2, 100, 1])["median"]), 51)

    def test_nonterminating_average_stays_exact(self):
        result = salary_summary([10000, 10001, 10001])
        self.assertEqual(exact(result["average"]), Fraction(30002, 3))
        self.assertEqual(result["average"]["amount"], "100.01")
        self.assertEqual(exact(salary_summary([0, 0, 1])["average"]), Fraction(1, 3))

    def test_empty_and_singleton_including_zero(self):
        empty = salary_summary([])
        self.assertEqual((empty["count"], empty["state"]), (0, "no_data"))
        self.assertTrue(all(empty[name] is None for name in STATISTICS))
        for salary in (0, 12346, MAX_MINOR):
            result = salary_summary([salary])
            self.assertEqual((result["count"], result["state"]), (1, "available"))
            self.assertEqual(result["spread"]["amount"], "0.00")
            for name in ("average", "median", "minimum", "maximum"):
                self.assertEqual(exact(result[name]), salary)

    def test_supported_population_and_money_bounds_with_small_hostile_decimal_context(self):
        values = [MAX_MINOR] * 9999 + [MAX_MINOR - 1]
        with localcontext() as context:
            context.prec = 2
            context.rounding = ROUND_DOWN
            context.traps[FloatOperation] = True
            result = salary_summary(values)
            self.assertEqual(result["average"]["amount"], "92233720368547758.07")
            self.assertEqual(exact(result["average"]), Fraction(MAX_MINOR * 10000 - 1, 10000))
            self.assertEqual(exact(result["median"]), MAX_MINOR)
            self.assertEqual(percentage(MAX_MINOR, 1), "922337203685477580700.00")
        # At 10,000 employees, these lie either side of a half-cent by only 1/10,000 cent.
        self.assertEqual(format_fraction(100004999, 1000000), "100.00")
        self.assertEqual(format_fraction(100005001, 1000000), "100.01")

    def test_exact_cross_multiplication_boundaries_and_rounded_display_do_not_classify(self):
        for salary, expected in ((79998, "below_80"), (79999, "below_80"), (80000, "80_to_120"),
                                 (120000, "80_to_120"), (120001, "above_120")):
            self.assertEqual(compa_bucket(salary, 100000), expected)
        self.assertEqual(percentage(79999, 100000), "80.00")
        self.assertEqual(compa_bucket(0, 100000), "below_80")
        self.assertEqual(compa_bucket(MAX_MINOR, MAX_MINOR), "80_to_120")
        self.assertEqual(compa_bucket(MAX_MINOR, 1), "above_120")

    def test_fraction_and_money_helpers_reject_float_bool_and_invalid_denominators(self):
        for value in (1.0, True, -1, MAX_MINOR + 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                salary_summary([value])
        for numerator, denominator in ((1.0, 2), (True, 2), (1, 0), (1, -1), (1, 2.0), (1, True)):
            for function in (format_fraction, percentage):
                with self.subTest(function=function, values=(numerator, denominator)), self.assertRaises(ValueError):
                    function(numerator, denominator)
        with self.assertRaises(ValueError):
            compa_bucket(1, 0)

    def test_deterministic_statistics_against_independent_integer_oracle(self):
        rng = random.Random(42)
        for count in (1, 2, 3, 4, 25, 50, 100, 10000):
            values = sorted(rng.randrange(MAX_MINOR + 1) for _ in range(count))
            result = salary_summary(reversed(values))
            median = Fraction(values[count // 2]) if count % 2 else Fraction(values[count // 2 - 1] + values[count // 2], 2)
            expected = {"average": Fraction(sum(values), count), "median": median,
                        "minimum": Fraction(values[0]), "maximum": Fraction(values[-1]), "spread": Fraction(values[-1] - values[0])}
            for name, value in expected.items():
                self.assertEqual(exact(result[name]), value)
                self.assertEqual(result[name]["amount"], reference_display(value))


class AnalyticsAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password_hash = hash_password("analytics-test-password")

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "salary.db"
        migrate(self.path)
        self.settings = Settings(self.path, "hr@example.com", self.password_hash, "http://testserver", cookie_secure=False)
        self.client = self.enterContext(TestClient(create_app(self.settings)))
        login = self.client.post("/auth/login", headers={"Origin": "http://testserver"},
                                 json={"email": "hr@example.com", "password": "analytics-test-password"})
        self.headers = {"Origin": "http://testserver", "X-CSRF-Token": login.json()["csrf_token"]}
        self.sequence = 0

    def employee(self, amount, country="India", level="L1", department="Engineering"):
        self.sequence += 1
        employee = Employee(f"EMP{self.sequence:05}", f"Person {self.sequence}", f"person{self.sequence}@example.com",
                            department, level, country, amount)
        with transaction(self.path) as connection:
            insert_employee(connection, employee)
        return employee

    def band(self, country="India", level="L1", midpoint=100000):
        with transaction(self.path) as connection:
            insert_band(connection, SalaryBand(country, level, midpoint // 2, midpoint, midpoint * 2))

    def summaries(self, **params):
        response = self.client.get("/analytics/summaries", params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def compa(self, **params):
        response = self.client.get("/analytics/compa-ratio", params=params)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(sum(bucket["count"] for bucket in result["buckets"]), result["eligible_count"])
        self.assertEqual(result["eligible_count"] + result["excluded_unbanded_count"], result["total_count"])
        self.assertEqual([bucket["key"] for bucket in result["buckets"]], ["below_80", "80_to_120", "above_120"])
        return result

    def test_empty_database_has_explicit_empty_countries_levels_and_no_eligible_data(self):
        result = self.summaries()
        self.assertEqual(result["total_count"], 0)
        self.assertEqual(len(result["countries"]), 6)
        self.assertEqual(len(result["levels"]), 48)
        self.assertEqual(result["departments"], [])
        for group in result["countries"] + result["levels"]:
            self.assertEqual((group["count"], group["state"]), (0, "no_data"))
            self.assertEqual(group["currency"], COUNTRY_CURRENCIES[group["country"]])
            self.assertTrue(all(group[name] is None for name in STATISTICS))
        compa = self.compa()
        self.assertEqual((compa["total_count"], compa["eligible_count"], compa["excluded_unbanded_count"]), (0, 0, 0))
        self.assertEqual(compa["state"], "no_eligible_data")
        self.assertTrue(all(bucket["percentage"] is None and bucket["count"] == 0 for bucket in compa["buckets"]))

    def test_even_median_half_cent_and_exact_average_are_exposed_over_http(self):
        self.employee(10000)
        self.employee(10001)
        result = self.summaries(country="India", department=" engineering ", level="L1")
        self.assertEqual(result["filters"], {"country": "India", "department": "engineering", "level": "L1"})
        for kind in ("countries", "departments", "levels"):
            self.assertEqual(len(result[kind]), 1)
            group = result[kind][0]
            self.assertEqual(group["count"], 2)
            for name in ("average", "median"):
                self.assertEqual(group[name], {"minor_numerator": "20001", "minor_denominator": "2", "amount": "100.01"})
            self.assertEqual(group["spread"]["amount"], "0.01")

    def test_department_and_level_groups_keep_all_six_country_currencies_separate(self):
        for index, country in enumerate(COUNTRY_CURRENCIES):
            self.employee((index + 1) * 10000, country, department="engineering" if index % 2 else "Engineering")
        result = self.summaries()
        self.assertEqual(result["total_count"], 6)
        for kind in ("countries", "departments", "levels"):
            populated = [group for group in result[kind] if group["count"]]
            self.assertEqual(len(populated), 6)
            self.assertEqual(sum(group["count"] for group in result[kind]), 6)
            for index, country in enumerate(COUNTRY_CURRENCIES):
                group = next(group for group in populated if group["country"] == country)
                self.assertEqual((group["currency"], group["count"]), (COUNTRY_CURRENCIES[country], 1))
                self.assertEqual(group["average"]["amount"], f"{(index + 1) * 100}.00")
                self.assertEqual(group["spread"]["amount"], "0.00")
        self.assertEqual({group["department_key"] for group in result["departments"]}, {"engineering"})

    def test_all_unbanded_still_count_in_every_salary_statistic(self):
        for salary in (0, 10000, 20000):
            self.employee(salary)
        group = self.summaries(country="India")["countries"][0]
        self.assertEqual(group["count"], 3)
        self.assertEqual(group["average"]["amount"], "100.00")
        self.assertEqual(group["median"]["amount"], "100.00")
        self.assertEqual(group["minimum"]["amount"], "0.00")
        self.assertEqual(group["maximum"]["amount"], "200.00")
        self.assertEqual(group["spread"]["amount"], "200.00")
        compa = self.compa()
        self.assertEqual((compa["eligible_count"], compa["excluded_unbanded_count"], compa["total_count"]), (0, 3, 3))
        self.assertEqual(compa["state"], "no_eligible_data")
        self.assertTrue(all(bucket["percentage"] is None for bucket in compa["buckets"]))

    def test_compa_boundaries_rounded_79999_and_out_of_band_salaries(self):
        self.band()
        for salary in (0, 79999, 80000, 100000, 120000, 120001, 999999):
            self.employee(salary)
        self.employee(999999, level="L2")
        compa = self.compa(country="India")
        self.assertEqual((compa["eligible_count"], compa["excluded_unbanded_count"], compa["total_count"]), (7, 1, 8))
        self.assertEqual([bucket["count"] for bucket in compa["buckets"]], [2, 3, 2])
        self.assertEqual([bucket["percentage"] for bucket in compa["buckets"]], ["28.57", "42.86", "28.57"])
        self.assertEqual(self.summaries(country="India")["countries"][0]["count"], 8)
        self.assertEqual(percentage(79999, 100000), "80.00")
        self.assertEqual(compa_bucket(79999, 100000), "below_80")

    def test_bucket_percentages_use_eligible_denominator_and_round_half_up(self):
        self.band()
        for number in range(32):
            self.employee(0 if number == 0 else 100000)
        self.employee(100000, level="L2")
        compa = self.compa()
        self.assertEqual((compa["eligible_count"], compa["total_count"]), (32, 33))
        self.assertEqual([bucket["percentage"] for bucket in compa["buckets"]], ["3.13", "96.88", "0.00"])

    def test_combined_filters_casefold_departments_and_unknown_department_empty_groups(self):
        self.employee(10000, department="Straße")
        self.employee(20000, department="STRASSE")
        self.employee(30000, level="L2", department="Straße")
        self.employee(40000, country="US", department="Straße")
        self.band()
        result = self.summaries(country=" India ", level=" L1 ", department=" STRASSE ")
        self.assertEqual(result["total_count"], 2)
        self.assertEqual(result["departments"][0]["department_key"], "strasse")
        self.assertEqual(result["countries"][0]["average"]["amount"], "150.00")
        self.assertEqual(self.compa(country="India", level="L1", department="strasse")["total_count"], 2)
        empty = self.summaries(country="US", department="Missing", level="L8")
        self.assertEqual(empty["total_count"], 0)
        for kind in ("countries", "departments", "levels"):
            self.assertEqual(len(empty[kind]), 1)
            group = empty[kind][0]
            self.assertEqual((group["count"], group["country"], group["currency"]), (0, "US", "USD"))
            self.assertTrue(all(group[name] is None for name in STATISTICS))
        self.assertEqual(self.summaries(country=" ", level="", department=" ")["total_count"], 4)

    def test_known_department_and_level_empty_groups_are_explicit(self):
        self.employee(10000, country="US", department="Sales", level="L2")
        result = self.summaries(country="India")
        self.assertEqual(result["departments"][0]["department"], "Sales")
        for group in result["countries"] + result["departments"] + result["levels"]:
            self.assertEqual(group["count"], 0)
            self.assertEqual(group["state"], "no_data")
            self.assertTrue(all(group[name] is None for name in STATISTICS))

    def test_search_pagination_sort_and_unsupported_filters_are_rejected(self):
        for path in ("/analytics/summaries", "/analytics/compa-ratio"):
            for params in ({"search": "Person"}, {"page": 2}, {"page_size": 25}, {"sort": "salary"},
                           {"country": "France"}, {"level": "L9"}, {"department": ["Sales", "Engineering"], "unknown": "x"}):
                response = self.client.get(path, params=params)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertIsInstance(response.json()["detail"], list)

    def test_authentication_no_store_and_explicit_response_schemas(self):
        with TestClient(create_app(self.settings)) as anonymous:
            self.assertEqual(anonymous.get("/analytics/summaries").status_code, 401)
            self.assertEqual(anonymous.get("/analytics/compa-ratio").status_code, 401)
        for path in ("/analytics/summaries", "/analytics/compa-ratio"):
            response = self.client.get(path)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(response.headers["x-content-type-options"], "nosniff")
            schema = self.client.app.openapi()["paths"][path]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
            self.assertIn("$ref", schema)

    def test_current_band_and_employee_mutations_recompute_without_changing_headcounts(self):
        employee = self.employee(100000)
        before = self.summaries()
        self.assertEqual(self.compa()["excluded_unbanded_count"], 1)
        self.band()
        self.assertEqual(self.compa()["eligible_count"], 1)
        with transaction(self.path) as connection:
            connection.execute("UPDATE salary_bands SET midpoint_minor=200000, revision=revision+1")
        self.assertEqual([bucket["count"] for bucket in self.compa()["buckets"]], [1, 0, 0])
        with transaction(self.path) as connection:
            connection.execute("DELETE FROM salary_bands")
        self.assertEqual(self.compa()["excluded_unbanded_count"], 1)
        self.assertEqual(self.summaries(), before)
        with transaction(self.path) as connection:
            connection.execute("UPDATE employees SET salary_minor=300000, revision=revision+1 WHERE employee_id=?", (employee.employee_id,))
        self.assertEqual(self.summaries(country="India")["countries"][0]["average"]["amount"], "3000.00")

    def test_population_and_department_catalog_share_snapshot_during_concurrent_write(self):
        self.employee(10000, department="Before")
        before = self.summaries()
        original = analytics.read_population
        def read_then_write(connection, filters):
            rows = original(connection, filters)
            with transaction(self.path) as writer:
                writer.execute("UPDATE employees SET department='After', salary_minor=99999, revision=revision+1")
            return rows
        with patch("app.analytics.read_population", side_effect=read_then_write):
            self.assertEqual(self.summaries(), before)
        after = self.summaries()
        self.assertEqual(after["departments"][0]["department"], "After")
        self.assertEqual(next(group for group in after["countries"] if group["country"] == "India")["average"]["amount"], "999.99")

    def test_compa_population_and_band_midpoints_are_one_snapshot(self):
        self.band()
        self.employee(100000)
        original = analytics.read_population
        def read_then_delete(connection, filters):
            rows = original(connection, filters)
            with transaction(self.path) as writer:
                writer.execute("DELETE FROM salary_bands")
            return rows
        with patch("app.analytics.read_population", side_effect=read_then_delete):
            self.assertEqual(self.compa()["eligible_count"], 1)
        self.assertEqual(self.compa()["eligible_count"], 0)

    def test_full_10000_population_filters_and_groups_reconcile_beyond_directory_pages(self):
        self.band(midpoint=10000)
        amounts = (7999, 8000, 12000, 12001)
        with transaction(self.path) as connection:
            for number in range(10000):
                insert_employee(connection, Employee(f"EMP{number:05}", f"Person {number}", f"person{number}@example.com",
                    "Engineering" if number % 2 == 0 else "Sales", "L1" if number < 7500 else "L2",
                    "India" if number < 9000 else "US", amounts[number % 4]))
        directory = self.client.get("/employees", params={"country": "India", "department": "Engineering"}).json()
        self.assertEqual((len(directory["items"]), directory["total"]), (25, 4500))
        result = self.summaries(country="India", department="Engineering")
        compa = self.compa(country="India", department="Engineering")
        self.assertEqual((result["total_count"], compa["total_count"]), (4500, 4500))
        for kind in ("countries", "departments", "levels"):
            self.assertEqual(sum(group["count"] for group in result[kind]), 4500)
        self.assertEqual((compa["eligible_count"], compa["excluded_unbanded_count"]), (3750, 750))
        self.assertEqual([bucket["count"] for bucket in compa["buckets"]], [1875, 1875, 0])
        self.assertEqual(exact(result["countries"][0]["average"]), Fraction(19999, 2))
        self.assertEqual(result["countries"][0]["median"]["amount"], "100.00")
        self.assertEqual(self.summaries()["total_count"], 10000)
        self.assertEqual(self.compa()["total_count"], 10000)

    def test_sqlite_max_values_are_accumulated_as_python_integers(self):
        for salary in (MAX_MINOR, MAX_MINOR, MAX_MINOR - 1):
            self.employee(salary)
        result = self.summaries(country="India")["countries"][0]
        self.assertEqual(exact(result["average"]), Fraction(3 * MAX_MINOR - 1, 3))
        self.assertEqual(result["average"]["amount"], "92233720368547758.07")
        self.assertEqual(result["spread"]["amount"], "0.01")
