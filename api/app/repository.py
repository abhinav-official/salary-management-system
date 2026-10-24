"""Persistence primitives; callers own a shared transaction for atomic batches."""

import sqlite3

from app.models import Employee, SalaryBand


def insert_employee(connection: sqlite3.Connection, employee: Employee):
    connection.execute(
        """INSERT INTO employees
           (employee_id, name, email, department, level, country, salary_minor, revision)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (employee.employee_id, employee.name, employee.email, employee.department,
         employee.level, employee.country, employee.salary_minor, employee.revision),
    )


def insert_band(connection: sqlite3.Connection, band: SalaryBand):
    connection.execute(
        """INSERT INTO salary_bands
           (country, level, minimum_minor, midpoint_minor, maximum_minor, revision)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (band.country, band.level, band.minimum_minor, band.midpoint_minor,
         band.maximum_minor, band.revision),
    )
