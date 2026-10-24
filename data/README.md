# Sample salary dataset

[employees_10000.xlsx](employees_10000.xlsx) contains synthetic data for the salary management requirements. All names are generated combinations, emails use example.com, and salary bands are illustrative test values rather than market benchmarks.

## Workbook contents

- **Employees:** 10,000 records with the required import headers: Employee ID, Name, Email, Department, Level, Country, and Annual Salary. IDs and emails are unique, including case-insensitive comparison. Select this worksheet for employee import.
- **Salary Bands:** all 48 country-and-level combinations, with Country, Level, Currency, Minimum, Midpoint, and Maximum columns.
- **About:** data definitions, expected counts, seed, and regeneration instructions.

| Country | Currency | Employees |
| --- | --- | ---: |
| India | INR | 4,000 |
| US | USD | 2,500 |
| UK | GBP | 1,200 |
| Germany | EUR | 1,000 |
| Singapore | SGD | 800 |
| UAE | AED | 500 |
| Total | | 10,000 |

Exactly 9,500 employees have salaries inside their matching band, including the minimum and maximum boundaries. Exactly 250 are strictly below the minimum, and exactly 250 are strictly above the maximum. Out-of-band salaries remain valid employee import values. All employees have a matching band; this fixture intentionally contains no Unbanded employees because all 48 bands are requested.

Levels L1–L8 are balanced within each country to within one employee. Departments are Engineering, Product, Design, Sales, Marketing, Finance, Human Resources, and Operations. Names, departments, salary positions, and employee ordering use a local pseudorandom generator with seed **42**. Names may repeat; IDs and emails never do.

## Salary and band rules

All amounts represent annual base salary in the country's currency. Generator arithmetic uses integer minor units only. Money is written as exact decimal **text** with two places and no separators or symbols, as accepted by the import requirements. This preserves cents without floating-point conversion. IDs are also text. The Employees worksheet derives currency from Country; Salary Bands has an explicit Currency column.

L1 midpoint amounts in major units are INR 600,000, USD 55,000, GBP 30,000, EUR 42,000, SGD 48,000, and AED 90,000. L1–L8 midpoint multipliers are respectively 100%, 135%, 180%, 240%, 320%, 425%, 560%, and 740% of the country's L1 midpoint. Minimum is 80% of midpoint and maximum is 120%. These band calculations are exact in minor units.

The generator shuffles a list containing exactly 9,500 inside-band, 250 below-band, and 250 above-band assignments. Inside salaries are sampled uniformly in minor units from minimum to maximum inclusive. Below salaries subtract between one minor unit and 25% of minimum; above salaries add between one minor unit and 25% of maximum. Assignment is global; no particular out-of-band quota per country is implied.

## Regenerate

From the repository root, using Python 3.10 or later:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python tools/generate_dataset.py
```

The generator always writes data/employees_10000.xlsx relative to its own repository, even when invoked from another working directory. Regeneration replaces the sample file and prints its SHA-256 hash.

The seed, generation order, workbook properties, ZIP entry timestamps, permissions, and entry order are fixed. ZIP entries are stored without compression to avoid compression-library differences. No current timestamp or machine-specific path is embedded in the workbook. With the pinned dependency and unchanged generator, repeated runs produce byte-identical files.

To verify two runs independently on macOS or Linux:

```sh
.venv/bin/python tools/generate_dataset.py
cp data/employees_10000.xlsx /tmp/employees_10000.first.xlsx
.venv/bin/python tools/generate_dataset.py
cmp /tmp/employees_10000.first.xlsx data/employees_10000.xlsx
shasum -a 256 /tmp/employees_10000.first.xlsx data/employees_10000.xlsx
```

`cmp` exits successfully with no output when the files are byte-identical. To check the distribution, read Employees and join each row to Salary Bands on Country and Level, comparing exact decimal amounts. Count inside using minimum ≤ salary ≤ maximum, below using salary < minimum, and above using salary > maximum; do not infer counts from row positions or the About notes.
