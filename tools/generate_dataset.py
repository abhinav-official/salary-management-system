#!/usr/bin/env python3
"""Generate the reproducible, synthetic salary-management sample workbook."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from random import Random
from zipfile import ZIP_STORED, ZipFile, ZipInfo

import xlsxwriter


SEED = 42
OUTPUT = Path(__file__).resolve().parents[1] / "data" / "employees_10000.xlsx"
# Country, currency, employee count, synthetic L1 annual midpoint in major units.
COUNTRIES = (
    ("India", "INR", 4_000, 600_000),
    ("US", "USD", 2_500, 55_000),
    ("UK", "GBP", 1_200, 30_000),
    ("Germany", "EUR", 1_000, 42_000),
    ("Singapore", "SGD", 800, 48_000),
    ("UAE", "AED", 500, 90_000),
)
LEVELS = tuple(f"L{number}" for number in range(1, 9))
LEVEL_PERCENTAGES = (100, 135, 180, 240, 320, 425, 560, 740)
DEPARTMENTS = (
    "Engineering", "Product", "Design", "Sales", "Marketing", "Finance",
    "Human Resources", "Operations",
)
FIRST_NAMES = (
    "Aarav", "Ananya", "Priya", "Rohan", "Alex", "Jordan", "Taylor", "Morgan",
    "Amelia", "Oliver", "Sophie", "James", "Lena", "Felix", "Elias", "Hannah",
    "Wei", "Mei", "Kai", "Jia", "Omar", "Layla", "Zain", "Noor",
)
LAST_NAMES = (
    "Shah", "Patel", "Rao", "Nair", "Smith", "Johnson", "Brown", "Davis",
    "Wilson", "Clarke", "Evans", "Wright", "Schmidt", "Weber", "Fischer",
    "König", "Tan", "Lim", "Lee", "Wong", "Hassan", "Ali", "Khalil", "Saeed",
)
EMPLOYEE_HEADERS = (
    "Employee ID", "Name", "Email", "Department", "Level", "Country",
    "Annual Salary",
)
BAND_HEADERS = ("Country", "Level", "Currency", "Minimum", "Midpoint", "Maximum")


def money_text(minor_units: int) -> str:
    """Serialize exact minor units as decimal text without floating-point money."""
    return f"{minor_units // 100}.{minor_units % 100:02d}"


def create_rows() -> tuple[list[tuple], list[tuple]]:
    rng = Random(SEED)
    bands = []
    band_lookup = {}
    population = []
    for country, currency, count, base_midpoint in COUNTRIES:
        for level, percentage in zip(LEVELS, LEVEL_PERCENTAGES):
            # Base midpoint is in major units; multiplying by a percentage also
            # performs the conversion to minor units (100 minor per major).
            midpoint = base_midpoint * percentage
            minimum = midpoint * 80 // 100
            maximum = midpoint * 120 // 100
            band_lookup[country, level] = (minimum, midpoint, maximum)
            bands.append((country, level, currency, money_text(minimum),
                          money_text(midpoint), money_text(maximum)))
        # Cover every level in every country, balanced to within one employee.
        population.extend((country, LEVELS[index % 8]) for index in range(count))

    rng.shuffle(population)
    categories = ["inside"] * 9_500 + ["below"] * 250 + ["above"] * 250
    rng.shuffle(categories)
    employees = []
    for index, ((country, level), category) in enumerate(zip(population, categories), 1):
        minimum, _, maximum = band_lookup[country, level]
        if category == "below":
            salary = minimum - rng.randint(1, minimum // 4)
        elif category == "above":
            salary = maximum + rng.randint(1, maximum // 4)
        else:
            salary = rng.randint(minimum, maximum)
        employees.append((
            f"EMP{index:06d}",
            f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}",
            f"emp{index:06d}@example.com",
            rng.choice(DEPARTMENTS), level, country, money_text(salary),
        ))
    return employees, bands


def write_table(workbook, name, headers, rows, widths, money_columns):
    sheet = workbook.add_worksheet(name)
    body = workbook.add_format({"font_name": "Arial", "font_size": 10, "valign": "vcenter"})
    money = workbook.add_format({
        "font_name": "Arial", "font_size": 10, "valign": "vcenter",
        "align": "right", "num_format": "@",
    })
    header = workbook.add_format({
        "font_name": "Arial", "font_size": 10, "bold": True,
        "font_color": "#FFFFFF", "bg_color": "#243746",
        "align": "center", "valign": "vcenter",
    })
    sheet.hide_gridlines(2)
    sheet.freeze_panes(1, 1)
    sheet.set_default_row(19)
    sheet.set_row(0, 26)
    for column, width in enumerate(widths):
        sheet.set_column(column, column, width)
    sheet.write_row(0, 0, headers, header)
    for row_number, row in enumerate(rows, 1):
        for column, value in enumerate(row):
            # Text is intentional for IDs and exact monetary import values.
            sheet.write_string(row_number, column, value,
                               money if column in money_columns else body)
    sheet.autofilter(0, 0, len(rows), len(headers) - 1)


def workbook_bytes(employees, bands) -> bytes:
    buffer = BytesIO()
    with xlsxwriter.Workbook(buffer, {"in_memory": True}) as workbook:
        workbook.set_properties({
            "title": "Salary management sample dataset",
            "author": "Dataset Generator",
            "comments": "Synthetic test data. Fixed seed 42.",
            "created": datetime(2000, 1, 1, tzinfo=timezone.utc),
        })
        write_table(workbook, "Employees", EMPLOYEE_HEADERS, employees,
                    (17, 25, 33, 22, 10, 16, 20), {6})
        write_table(workbook, "Salary Bands", BAND_HEADERS, bands,
                    (16, 10, 13, 20, 20, 20), {3, 4, 5})
        about = workbook.add_worksheet("About")
        about.hide_gridlines(2)
        about.set_column(0, 0, 27)
        about.set_column(1, 1, 94)
        about.set_default_row(24)
        heading = workbook.add_format({"font_name": "Arial", "font_size": 14, "bold": True})
        label = workbook.add_format({"font_name": "Arial", "font_size": 10, "bold": True})
        text = workbook.add_format({"font_name": "Arial", "font_size": 10})
        about.write_string(1, 0, "Sample dataset", heading)
        notes = [
            ("Source", "Synthetic employees and illustrative bands; not market salary research."),
            ("Random seed", SEED),
            ("Employees", len(employees)),
            ("Salary bands", len(bands)),
            ("Salary basis", "Annual base salary in the employee country's currency."),
            ("Money representation", "Exact decimal text with two places; calculated as integer minor units."),
            ("Inside band", "9,500 employees; minimum and maximum boundaries are inclusive."),
            ("Below minimum", "250 employees; salary is strictly below the matching band minimum."),
            ("Above maximum", "250 employees; salary is strictly above the matching band maximum."),
            ("Band coverage", "All 48 country/level pairs are present; no employee is Unbanded."),
            ("Levels", "L1 to L8, balanced within each country to within one employee."),
            ("Email addresses", "Unique example.com addresses; no real contact data."),
            ("Import worksheet", "Use Employees. Salary Bands and About are not employee import rows."),
            ("Regenerate", "python tools/generate_dataset.py (see data/README.md for setup)."),
            ("Reproducibility", "Fixed metadata, ZIP timestamps and entry order; no clock-dependent values."),
        ]
        notes.extend((country, count) for country, _, count, _ in COUNTRIES)
        for row_number, (key, value) in enumerate(notes, 3):
            about.write_string(row_number, 0, key, label)
            about.write(row_number, 1, value, text)

    # Normalize the XLSX container as well as its content. Stored entries avoid
    # compression-library variation, and fixed metadata avoids wall-clock data.
    canonical = BytesIO()
    with ZipFile(buffer) as source, ZipFile(canonical, "w") as target:
        for name in sorted(source.namelist()):
            entry = ZipInfo(name, date_time=(2000, 1, 1, 0, 0, 0))
            entry.compress_type = ZIP_STORED
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            target.writestr(entry, source.read(name))
    return canonical.getvalue()


def main() -> None:
    employees, bands = create_rows()
    content = workbook_bytes(employees, bands)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_bytes(content)
    print(f"Created {OUTPUT} ({len(employees):,} employees, {len(bands)} bands)")
    print(f"SHA-256: {hashlib.sha256(content).hexdigest()}")


if __name__ == "__main__":
    main()
