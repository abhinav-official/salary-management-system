# Salary Management System Requirements

## 1. Purpose and scope

The system enables an HR Manager to maintain a directory of 10,000 employees, manage salary bands, import employees from Excel, inspect compensation analytics and equity, and export the directory to CSV.

This document specifies observable behavior and business rules only. It does not select a technology stack, architecture, storage product, or implementation approach. No application code is part of this phase.

### Working product defaults

The following defaults resolve details not specified in the initial brief and can be revised during requirements review:

- Salary means annual base salary, excluding bonuses, benefits, deductions, and payroll payments. Bands use the same annual basis.
- Excel import creates new employees only. Updates to existing employees use the edit workflow.
- An equity comparison requires at least one other eligible employee in the same country and level. A group of two employees therefore permits a comparison for each employee.
- Pay spread means maximum salary minus minimum salary, expressed in the group's currency.
- Employee IDs are unique and immutable after creation. Email addresses are unique without regard to letter case.
- Departments are nonempty names; managing a separate department catalog is outside this scope.

## 2. Users, access, and scope boundaries

- The primary user is an authorized HR Manager with access to directory, bands, imports, analytics, equity, and exports.
- Employee and salary information must be available only to authorized users. Access restrictions apply equally to reads, changes, imports, and downloads.
- The system must present validation failures, empty results, insufficient data, and operational failures clearly, without implying that a failed change succeeded.
- Payroll processing, tax calculations, currency conversion, benefits, employee self-service, approval workflows, salary history, and multiple HR permission tiers are outside the initial scope.

## 3. Employee data and validation

Each employee has the following required fields:

| Field | Requirement |
| --- | --- |
| Employee ID | Nonempty unique text identifier; preserve meaningful leading zeros. |
| Name | Nonempty full name; support international characters. |
| Email | Valid email address, unique across all employees regardless of case. |
| Department | Nonempty department name. |
| Level | One of L1, L2, L3, L4, L5, L6, L7, or L8. |
| Country | One of the six supported countries listed below. |
| Annual salary | Nonnegative monetary amount in the country's currency, governed by Section 4. |

- Trim surrounding whitespace in text fields before validation and uniqueness checks. Treat employee ID uniqueness as case-insensitive; retain the entered spelling for display.
- Department names differing only in case or surrounding whitespace must not create distinct filter or analytics groups.
- Country determines currency; an employee cannot select a different salary currency independently.
- A salary outside a configured band's minimum or maximum is allowed and must remain visible for analysis.
- A missing country-and-level band must never prevent saving or importing an otherwise valid employee.
- Creation, editing, and import must apply the same field and money validation rules.
- Invalid edits must leave the previously saved employee unchanged. Conflicting concurrent edits must be reported instead of silently discarding another saved change.

## 4. Countries, currencies, and exact money

| Country | Currency | Minor unit per major unit |
| --- | --- | --- |
| India | INR | 100 |
| US | USD | 100 |
| UK | GBP | 100 |
| Germany | EUR | 100 |
| Singapore | SGD | 100 |
| UAE | AED | 100 |

- Store salaries and band amounts as integer minor units. Never use floating-point money in parsing, storage, calculation, import, or export.
- Monetary inputs must be interpreted as exact decimal amounts. Reject nonnumeric, ambiguous, non-finite, or unsupported values with a field-specific error.
- Round monetary input to two decimal places using round-half-up, then represent the result as integer minor units. For example, 123.454 becomes 123.45, and 123.455 becomes 123.46.
- Reject negative salary inputs before rounding, including negative values that would round to zero.
- Calculations that produce fractional minor units, including averages and even-count medians, must retain exact fractional values until final presentation. Round the final monetary result half-up to two decimal places; do not truncate intermediate results or round each contribution.
- Show amounts with two decimal places and an unambiguous currency code. Exact valid amounts must not lose precision or overflow silently; unsupported numeric sizes must produce explicit validation errors.
- Percentage displays use two decimal places with half-up rounding. Classifications and eligibility decisions use exact, unrounded values.
- Never add, average, rank, or take a median of monetary amounts from different currencies as if they were comparable. Global employee counts may combine countries; monetary summaries must remain separated by country and currency.

## 5. Employee directory

### Create, view, edit, and delete

- Allow the HR Manager to create an employee, view all employee fields and band status, and edit all fields except the employee ID.
- Show actionable errors for missing fields, invalid values, duplicate IDs, and duplicate emails. Do not silently overwrite an employee.
- Require explicit confirmation identifying the employee before deletion. A canceled deletion must leave the record unchanged.
- A successful deletion removes the employee from the current directory, exports, analytics, and equity populations. Historical record retention is outside this scope.
- Reflect successful changes in directory counts, band status, analytics, and equity without requiring a new sign-in.

### Search, filters, and sorting

- Provide a single search across employee ID, name, and email using case-insensitive substring matching after trimming surrounding query whitespace. A match in any of these fields includes the employee.
- Provide department, level, and country filters with an all-values option. Each filter may select one value; different filters combine with AND, together with the search condition.
- Support ascending and descending sorting by employee ID, name, email, department, level, country, and salary. Level order is L1 through L8.
- Use employee ID ascending as the default sort and as the deterministic tie-breaker for other sorts.
- Salary ordering across countries must group by country first and order by local salary within each country; it must not imply a comparison of currency values. Communicate this ordering to the user.
- Applying or clearing search, filters, or sorting must refresh results and reset pagination to the first page.

### Pagination

- Use server-side pagination so the requested page is retrieved without loading all 10,000 employee records into the directory screen.
- Show 25 rows per page by default, with 50 and 100 as the other options. Changing page size resets to the first page.
- Show the total matching count, current visible range, and navigation between available pages. Disable navigation beyond the first or last page.
- Apply search, filters, and sorting to the complete matching dataset before pagination.
- Retain search, filters, and sort while navigating pages. If deletion makes the current page invalid, move to the last valid page.
- Show a clear empty state when no records match. Stable, unchanged data must not produce missing or duplicated rows across pages.

## 6. Salary bands and Unbanded employees

- A salary band is uniquely identified by country and level. At most one band may exist for each pair, for 48 possible combinations across six countries and eight levels.
- Allow the HR Manager to list, create, view, edit, and delete bands. The system may have fewer than 48 configured bands; missing bands are valid states.
- Each band contains minimum, midpoint, and maximum annual salary amounts in the country's currency.
- Require 0 ≤ minimum ≤ midpoint ≤ maximum and a strictly positive midpoint. Apply the exact-money rules before checking the ordering; reject negative inputs before rounding.
- Reject duplicate country-and-level bands rather than overwriting them. Identify the country, level, and currency in band forms and listings.
- An employee is Band-matched when a band exists for their exact country and level; otherwise they are Unbanded. Status is derived from current employee and band data.
- Unbanded employees save and import successfully. Include them in directory results, exports, employee counts, average salary, median salary, minimum, maximum, and pay spread.
- Exclude Unbanded employees only from compa-ratio and equity calculations. Show the excluded Unbanded count in both views for the selected population, including when every employee is excluded.
- Deleting a band requires explicit confirmation explaining that affected employees become Unbanded. Never delete those employees or change their salaries.
- Creating, editing, or deleting a band, or changing an employee's country or level, must update band status and affected calculations. Changing country requires explicit confirmation of a salary amount in the new currency; no automatic currency conversion is permitted.

## 7. Excel import

### File contract

- Accept Excel workbooks in .xlsx format with one employee per data row on a single identified employee worksheet.
- Require named columns for Employee ID, Name, Email, Department, Level, Country, and Annual Salary. Column order is flexible; duplicate or missing required headers are errors.
- Clearly show the expected worksheet, headers, accepted country and level values, and salary format before upload. If multiple possible employee worksheets exist, require selection before preview.
- Preserve employee IDs as text, including leading zeros. Reject numeric IDs whose original text cannot be recovered reliably. Reject formulas in required cells and do not execute workbook content.
- Require salary values to be unambiguous decimal amounts without currency symbols or thousands separators; the country supplies the currency. Validate the exact amount represented by the cell without introducing floating-point money.
- Ignore wholly blank rows, but retain original Excel row numbering for all remaining rows. Partially populated rows are subject to normal validation. Report a workbook with no employee rows as invalid.
- Report unreadable, corrupt, unsupported, or structurally invalid files clearly. Do not modify employee records during file inspection.

### Phase 1: Preview and validation

- Validate every data row before making the import confirmable. Continue after an invalid row so the preview reports all detected problems across the workbook.
- Show each proposed employee, the interpreted values, monetary normalization or rounding, and Band-matched or Unbanded status.
- Report row-specific problems with the worksheet name, original Excel row number, field, and clear reason. For example, if headers are on row 1, the first employee's errors refer to row 2.
- Validate required fields, allowed countries and levels, exact-money rules, and duplicate IDs or emails both within the workbook and against existing employees.
- Mark all rows involved in an intra-workbook duplicate. An existing ID or email is an error even when the proposed values are identical to the existing record.
- Show total data rows, valid rows, invalid rows, all errors, and the count of Unbanded employees. A row with several errors counts once as an invalid row.
- Missing bands are informational and do not make a row invalid. Salaries outside a band's range are also allowed.
- Preview must create, change, and delete no employees. Any invalid row or file-level error makes the entire preview unclean and disables confirmation.
- Corrections require a new preview. A clean preview means the complete proposed import has no validation errors, not merely that a sampled page is clean.

### Phase 2: Confirm and atomic creation

- Confirmation is available only for a clean preview and must import exactly the reviewed employee values from that preview.
- Reject confirmation without a valid clean preview, for a changed file or changed proposed values, or for an expired or already completed preview. State clearly when another preview is required.
- Recheck relevant validation and uniqueness against current records at confirmation. If intervening employee changes introduce any conflict, reject the entire import and require a refreshed preview; never silently overwrite, skip, or partially create records.
- If band changes alter the reviewed band status before confirmation, require a refreshed preview so the user sees current status. A missing band remains valid.
- Create every proposed employee together or create none. A validation, storage, or operational failure must leave zero employees from the attempted import saved.
- Repeated clicks, retries, or an uncertain completion response must never create duplicate employees or a second import. Show whether the reviewed import completed or requires retry or a new preview.
- On success show the number created and the Unbanded count, and refresh directory and analytics totals. On failure show actionable details without a success message.
- Support an import containing 10,000 employee rows with complete validation and access to every reported error. Large previews may paginate their display but must validate all rows.

## 8. Analytics

### Population and grouping

- Compute analytics from all saved employees in the selected analytics population, never from a directory page or unconfirmed import.
- Provide department, level, and country filters for analytics and equity, combined with AND. Show the active filters and population count. Directory search and pagination do not implicitly restrict these views.
- Provide salary summaries per country, per department within each country, and per level within each country. Always label country and currency; a department or level spanning countries must have separate monetary summaries per country.
- Include Unbanded employees in every ordinary salary statistic and headcount. A missing band is unrelated to eligibility for these statistics.

### Salary statistics

For every selected group, show employee count, average, median, minimum, maximum, and pay spread:

- Average is the sum of salaries divided by employee count.
- Median is the middle salary after ascending salary ordering for an odd count. For an even count, it is the arithmetic mean of the two middle salaries.
- Pay spread is maximum minus minimum. A group containing one employee has a spread of zero and its salary as both average and median.
- A group with no employees shows a zero count and an explicit no-data state for monetary statistics, not a misleading zero salary.
- Retain exact intermediate values and round only final monetary displays according to Section 4.

### Compa-ratio

- An employee's compa-ratio is their salary divided by their matching band's midpoint, expressed as a percentage. Use the current matching band regardless of whether salary is inside its range.
- Use exactly three buckets, classified using the unrounded ratio:

| Bucket | Exact membership |
| --- | --- |
| Below 80% | Ratio strictly less than 80%. |
| 80 to 120% | Ratio greater than or equal to 80% and less than or equal to 120%. |
| Above 120% | Ratio strictly greater than 120%. |

- Show each bucket's count and percentage of eligible Band-matched employees, plus eligible count, excluded Unbanded count, and total population count.
- Bucket counts must sum to the eligible count; eligible count plus excluded Unbanded count must equal the total selected employee count.
- If no employees are eligible, show zero bucket counts and a no-eligible-data state for percentages. Do not divide by zero.

## 9. Equity view

- Provide an employee-level view showing ID, name, country, level, currency, salary, peer count, peer median, and percentage difference from the peer median, or an explicit unavailable state.
- A target employee and their peers must be Band-matched. Unbanded employees remain represented in the selected population and excluded count, with an Unbanded state and no equity number.
- Peers are all other saved eligible employees in the target's exact country and level. Exclude the target employee themselves. Department does not define the peer group.
- Analytics and equity filters select the employees being reported; they must not shrink the peer reference population. Clearly explain that peer comparisons use the full saved country-and-level population.
- Compute the peer median using the same odd/even rules as analytics, retaining any fractional minor unit until the final display.
- Percentage difference is 100 × (employee salary minus peer median) divided by peer median. A positive value means above peers, a negative value means below, and zero means equal.
- With no other eligible peer, show Insufficient peers and no calculated percentage. Do not substitute the employee's own salary or the band midpoint.
- If eligible peers exist but their median is zero, show Peer median is zero and no percentage; retain the actual peer count and median.
- Show counts for employees with an available comparison, excluded Unbanded employees, insufficient peers, and zero peer median. These mutually exclusive counts must reconcile to the selected population total.
- Changes to employee salary, country, level, creation, deletion, or bands must be reflected in affected peer groups and equity results.

## 10. CSV export

- Export the full matching employee directory using the exact same search and department, level, and country filter semantics as the screen, independent of its current page and page size.
- Preserve the screen's selected sorting and deterministic tie-breaker in the export.
- Include Employee ID, Name, Email, Department, Level, Country, Currency, Annual Salary, and Band Status, with one employee per row and a header row.
- Export salaries as exact decimal amounts with two decimal places, without thousands separators or currency symbols; the separate currency column identifies the currency.
- Preserve international text and meaningfully quoted commas, quotes, and newlines. Protect text fields from spreadsheet formula execution while keeping their intended values understandable.
- Use one consistent snapshot of the matching records for each export, so concurrent changes cannot create missing or duplicate rows within it.
- An empty result exports headers only. An export failure must be reported, not presented as a successful empty or truncated file.

## 11. Quality and usability requirements

- Support a directory of 10,000 saved employees and full-population analytics, equity, imports, and exports at that scale.
- Search and pagination must retrieve only the requested page while accurately counting the full matching population. Preview validation, analytics, equity, and export must not be limited by visible rows.
- Show progress or loading states for longer operations and prevent accidental duplicate submissions. A failed operation must preserve the user's entered criteria or explain how to recover.
- Label fields, currencies, validation messages, and empty states clearly. Core workflows must support keyboard use, readable error text, and status indicators that do not rely on color alone.
- Directory, import, export, and analytics must use consistent employee validation, currency meanings, band status, and population rules.
- Do not expose sensitive employee records, uploaded workbook content, or exported files to unauthorized users through error messages or operational diagnostics.

## 12. Acceptance scenarios

The following scenarios supplement, rather than replace, the requirements above:

| Scenario | Expected outcome |
| --- | --- |
| Directory contains 10,000 employees with no filters. | First page shows 25 records; total is 10,000; 50 and 100 page sizes are available. |
| Search and all three filters narrow the directory. | Count, rows, and CSV contain exactly the same matching population; CSV includes every page. |
| Two matching employees have the same selected sort value. | Employee ID resolves their order consistently across pages and export. |
| Salary input is 123.455 in a supported currency. | Save 12,346 minor units and show 123.46; preview shows this normalized amount. |
| Two salaries are 100.00 and 100.01. | Exact average and median are 100.005, each displayed as 100.01; spread is 0.01. |
| A department has employees in India and the US. | Show separate INR and USD monetary summaries; never one blended average or median. |
| An employee has a valid country and level but no matching band. | Create and import succeed; all ordinary totals include them; compa-ratio and equity show their exclusion. |
| Every employee in a selected population is Unbanded. | Headcount and salary statistics remain available; compa-ratio and equity show all employees excluded with no fabricated percentages. |
| Midpoint is 100.00; salaries are 79.99, 80.00, 120.00, and 120.01. | Bucket counts are 1 below, 2 in 80 to 120%, and 1 above. |
| Midpoint is 1,000.00; salary is 799.99. | Exact ratio 79.999% is below 80%, even though the displayed ratio rounds to 80.00%. |
| An Excel row 7 has an invalid email and row 12 duplicates an existing ID. | Preview reports both original Excel row numbers, validates all other rows, and prevents confirmation; no records change. |
| A workbook contains two rows with the same normalized email. | Both rows are marked invalid; neither is skipped or merged. |
| A clean preview becomes conflicting because another employee uses a proposed email. | Confirm fails for the whole import and requests a refreshed preview; zero proposed records are created. |
| A valid 10,000-row import encounters a failure during confirmation. | Either all 10,000 are saved successfully or none are saved; success is never partial. |
| Confirmation is retried after it completed. | No additional employees are created; the completed outcome is reported. |
| A country-and-level group has salaries 100.00, 200.00, and 400.00. | For the 100.00 employee, peer median is 300.00 and equity difference is -66.67%; their own salary is excluded. |
| An employee is the sole member of their country-and-level group. | Show Insufficient peers with peer count zero. |
| An employee has exactly one other eligible peer. | Use that other employee's salary as peer median. |
| A peer median is zero. | Show the explicit zero-median state without dividing by zero. |
| A band is deleted and later recreated for an employee's country and level. | Employee remains saved throughout; status and eligibility change from Band-matched to Unbanded and back. |

## 13. Requirements-stage deliverables

- Maintain this requirements document at docs/REQUIREMENTS.md.
- Include a repository .gitignore for local environment secrets, operating-system metadata, editor artifacts, and logs without selecting an application stack.
- Initialize the repository on main and commit these requirements-stage files.
- Implementation, stack selection, and application scaffolding follow a later instruction.
