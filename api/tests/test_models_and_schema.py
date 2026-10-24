import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from app.database import SCHEMA_VERSION, check_ready, connect, transaction
from app.migrate import migrate
from app.models import COUNTRY_CURRENCIES, Employee, SalaryBand
from app.money import MAX_MINOR, parse_money
from app.repository import insert_band, insert_employee


class ModelAndSchemaTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "salary.db"
        migrate(self.path)
        self.employee = Employee("EMP0001", "Müller Rao", "Person@example.com",
                                 "Engineering", "L1", "India", 12346)
        self.band = SalaryBand("India", "L1", 10000, 20000, 30000)

    def employee_row(self):
        with connect(self.path) as connection:
            return connection.execute("SELECT * FROM employees").fetchone()

    def test_normalization_preserves_display_and_leading_zeros(self):
        employee = replace(self.employee, employee_id=" 0001 ", name=" Müller Rao ",
                           email=" Person@EXAMPLE.com ", department="\u2003Engineering\u2003")
        with transaction(self.path) as connection:
            insert_employee(connection, employee)
        row = self.employee_row()
        self.assertEqual((row["employee_id"], row["employee_id_key"]), ("0001", "0001"))
        self.assertEqual(row["name"], "Müller Rao")
        self.assertEqual((row["email"], row["email_key"]), ("Person@EXAMPLE.com", "person@example.com"))
        self.assertEqual((row["department"], row["department_key"]), ("Engineering", "engineering"))
        self.assertEqual(employee.currency, "INR")

    def test_case_insensitive_id_and_email_uniqueness(self):
        with transaction(self.path) as connection:
            insert_employee(connection, self.employee)
        duplicates = (
            replace(self.employee, employee_id=" emp0001 ", email="other@example.com"),
            replace(self.employee, employee_id="EMP0002", email=" person@EXAMPLE.COM "),
        )
        for employee in duplicates:
            with self.subTest(employee=employee), self.assertRaises(sqlite3.IntegrityError):
                with transaction(self.path) as connection:
                    insert_employee(connection, employee)
        self.assertEqual(self.employee_row()["salary_minor"], 12346)

    def test_generated_keys_protect_direct_sql_from_drift_and_duplicates(self):
        with transaction(self.path) as connection:
            insert_employee(connection, self.employee)
        with self.assertRaises(sqlite3.OperationalError), transaction(self.path) as connection:
            connection.execute("UPDATE employees SET email_key='forged', revision=2")
        with self.assertRaises(sqlite3.IntegrityError), transaction(self.path) as connection:
            connection.execute("""INSERT INTO employees
                (employee_id, name, email, department, level, country, salary_minor)
                VALUES ('different', 'Other', 'PERSON@EXAMPLE.COM', 'Sales', 'L2', 'US', 100)""")
        with transaction(self.path) as connection:
            connection.execute("UPDATE employees SET email='New@EXAMPLE.COM', revision=2")
        self.assertEqual(self.employee_row()["email_key"], "new@example.com")

    def test_unicode_casefold_uniqueness_and_department_grouping(self):
        first = replace(self.employee, employee_id="Straße", department="People Operations")
        second = replace(self.employee, employee_id="STRASSE", email="second@example.com")
        with transaction(self.path) as connection:
            insert_employee(connection, first)
        with self.assertRaises(sqlite3.IntegrityError), transaction(self.path) as connection:
            insert_employee(connection, second)
        with transaction(self.path) as connection:
            insert_employee(connection, replace(second, employee_id="EMP0002",
                                                department=" people OPERATIONS "))
        with connect(self.path) as connection:
            groups = connection.execute("SELECT department_key, count(*) FROM employees GROUP BY department_key").fetchall()
        self.assertEqual([tuple(row) for row in groups], [("people operations", 2)])

    def test_employee_validation_rejects_invalid_fields_and_minor_types(self):
        for changes in ({"employee_id": " "}, {"employee_id": 123}, {"name": ""},
                        {"name": "bad\x00name"}, {"email": "not-an-email"},
                        {"department": "\t "}, {"country": "France"}, {"level": "L9"},
                        {"salary_minor": -1}, {"salary_minor": 123.0},
                        {"salary_minor": True}, {"salary_minor": MAX_MINOR + 1},
                        {"revision": 0}, {"revision": 1.0}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(self.employee, **changes)

    def test_missing_band_is_valid_and_no_foreign_key_is_present(self):
        with transaction(self.path) as connection:
            insert_employee(connection, self.employee)
        with connect(self.path) as connection:
            self.assertEqual(connection.execute("PRAGMA foreign_key_list(employees)").fetchall(), [])
            row = connection.execute("""SELECT e.salary_minor, b.midpoint_minor
                FROM employees e LEFT JOIN salary_bands b
                ON e.country=b.country AND e.level=b.level""").fetchone()
        self.assertEqual(tuple(row), (12346, None))

    def test_band_removal_preserves_employee_and_out_of_band_salary(self):
        with transaction(self.path) as connection:
            insert_band(connection, self.band)
            insert_employee(connection, replace(self.employee, salary_minor=999999))
            connection.execute("DELETE FROM salary_bands")
        self.assertEqual(self.employee_row()["salary_minor"], 999999)

    def test_integer_money_zero_and_maximum_round_trip(self):
        for index, amount in enumerate((0, parse_money("123.455"), MAX_MINOR)):
            with transaction(self.path) as connection:
                insert_employee(connection, replace(self.employee, employee_id=f"EMP{index}",
                                                    email=f"person{index}@example.com", salary_minor=amount))
        with connect(self.path) as connection:
            rows = connection.execute("SELECT salary_minor, typeof(salary_minor) FROM employees ORDER BY salary_minor").fetchall()
        self.assertEqual([tuple(row) for row in rows], [(0, "integer"), (12346, "integer"), (MAX_MINOR, "integer")])

    def test_database_rejects_invalid_employee_values(self):
        for field, value in (("employee_id", None), ("name", " "), ("email", " "),
                             ("department", ""), ("country", "France"), ("level", "L0"),
                             ("salary_minor", -1), ("salary_minor", 123.45),
                             ("salary_minor", "NaN"), ("salary_minor", None),
                             ("revision", 0), ("revision", 1.5)):
            with self.subTest(field=field, value=value), self.assertRaises(sqlite3.IntegrityError):
                with transaction(self.path) as connection:
                    insert_employee(connection, self.employee)
                    if field == "revision":
                        connection.execute("DELETE FROM employees")
                        connection.execute("""INSERT INTO employees
                            (employee_id, name, email, department, country, level, salary_minor, revision)
                            VALUES ('EMP', 'Name', 'name@example.com', 'Sales', 'US', 'L1', 10, ?)""", (value,))
                    else:
                        # Field names above are a closed test constant, never user input.
                        connection.execute(f"UPDATE employees SET {field}=?, revision=2", (value,))

    def test_band_order_positive_midpoint_and_integer_validation(self):
        for amounts in ((20000, 10000, 30000), (10000, 30000, 20000), (0, 0, 0),
                        (-1, 20000, 30000), (1, 20000.0, 30000), (1, 2, MAX_MINOR + 1)):
            with self.subTest(amounts=amounts), self.assertRaises(ValueError):
                SalaryBand("India", "L1", *amounts)
        for amounts in ((20000, 10000, 30000), (10000, 30000, 20000), (0, 0, 0),
                        (-1, 20000, 30000), (10000, 20000, 30000.5)):
            with self.subTest(amounts=amounts), self.assertRaises(sqlite3.IntegrityError):
                with transaction(self.path) as connection:
                    connection.execute("""INSERT INTO salary_bands
                        (country, level, minimum_minor, midpoint_minor, maximum_minor)
                        VALUES ('India', 'L1', ?, ?, ?)""", amounts)

    def test_all_48_band_pairs_currencies_and_duplicate_constraint(self):
        with transaction(self.path) as connection:
            for country, currency in COUNTRY_CURRENCIES.items():
                for number in range(1, 9):
                    band = SalaryBand(country, f"L{number}", 0, 1, 1)
                    self.assertEqual(band.currency, currency)
                    insert_band(connection, band)
        with self.assertRaises(sqlite3.IntegrityError), transaction(self.path) as connection:
            insert_band(connection, self.band)
        with connect(self.path) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM salary_bands").fetchone()[0], 48)

    def test_employee_identity_and_revision_constraints(self):
        with transaction(self.path) as connection:
            insert_employee(connection, self.employee)
        for statement in ("UPDATE employees SET employee_id='changed', revision=2",
                          "UPDATE employees SET salary_minor=10",
                          "UPDATE employees SET salary_minor=10, revision=3"):
            with self.subTest(statement=statement), self.assertRaises(sqlite3.IntegrityError):
                with transaction(self.path) as connection:
                    connection.execute(statement)
        with transaction(self.path) as connection:
            updated = connection.execute("""UPDATE employees SET salary_minor=10, revision=revision+1
                WHERE employee_id_key='emp0001' AND revision=1""").rowcount
            stale = connection.execute("""UPDATE employees SET salary_minor=20, revision=revision+1
                WHERE employee_id_key='emp0001' AND revision=1""").rowcount
        self.assertEqual((updated, stale), (1, 0))
        self.assertEqual((self.employee_row()["salary_minor"], self.employee_row()["revision"]), (10, 2))

    def test_band_revision_constraint(self):
        with transaction(self.path) as connection:
            insert_band(connection, self.band)
        with self.assertRaises(sqlite3.IntegrityError), transaction(self.path) as connection:
            connection.execute("UPDATE salary_bands SET midpoint_minor=25000")
        with transaction(self.path) as connection:
            connection.execute("UPDATE salary_bands SET midpoint_minor=25000, revision=2 WHERE revision=1")
        with connect(self.path) as connection:
            row = connection.execute("SELECT midpoint_minor, revision FROM salary_bands").fetchone()
        self.assertEqual(tuple(row), (25000, 2))

    def test_batch_write_failure_rolls_back_employee_and_band(self):
        with self.assertRaises(sqlite3.IntegrityError):
            with transaction(self.path) as connection:
                insert_band(connection, self.band)
                insert_employee(connection, self.employee)
                insert_employee(connection, self.employee)
        with connect(self.path) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM employees").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM salary_bands").fetchone()[0], 0)


class MigrationUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "salary.db"
        migration = Path(__file__).parents[1] / "app/migrations/0001_sessions.sql"
        with connect(self.path) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(migration.read_text())
            connection.execute("PRAGMA user_version=1")
            connection.execute("INSERT INTO sessions VALUES ('old-token-hash', 'old-csrf', 2000000000)")

    def restart_migration(self):
        env = {**os.environ, "DATABASE_PATH": str(self.path)}
        result = subprocess.run([sys.executable, "-m", "app.migrate"],
                                cwd=Path(__file__).parents[1], env=env,
                                capture_output=True, text=True, check=True)
        self.assertIn(f"schema version {SCHEMA_VERSION}", result.stdout)

    def test_v1_upgrade_and_process_restarts_preserve_all_records(self):
        self.restart_migration()
        with transaction(self.path) as connection:
            insert_employee(connection, Employee("0001", "Name", "name@example.com",
                                                "Sales", "L1", "India", 12346))
            insert_band(connection, SalaryBand("India", "L1", 10000, 20000, 30000))
        self.restart_migration()
        self.restart_migration()
        check_ready(self.path)
        with connect(self.path) as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(tuple(connection.execute("SELECT * FROM sessions").fetchone()),
                             ("old-token-hash", "old-csrf", 2000000000))
            self.assertEqual(tuple(connection.execute("SELECT employee_id, salary_minor, revision FROM employees").fetchone()),
                             ("0001", 12346, 1))
            self.assertEqual(tuple(connection.execute("SELECT minimum_minor, midpoint_minor, maximum_minor FROM salary_bands").fetchone()),
                             (10000, 20000, 30000))
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_v2_migration_failure_rolls_back_schema_and_retains_v1_session(self):
        with connect(self.path) as connection:
            connection.execute("CREATE TABLE salary_bands (conflicting TEXT)")
        with self.assertRaises(sqlite3.OperationalError):
            migrate(self.path)
        with connect(self.path) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='employees'").fetchone())
            self.assertEqual(connection.execute("SELECT count(*) FROM sessions").fetchone()[0], 1)
