# Salary Management System Build Plan

## 1. Scope and execution rules

Build the system defined in [REQUIREMENTS.md](REQUIREMENTS.md), using the committed [sample workbook](../data/employees_10000.xlsx) and its [data definitions](../data/README.md). The requirements remain the authority for business behavior; this plan specifies implementation order, ownership, and verification.

This change adds a plan only. Application implementation begins with Step 1 after a subsequent instruction.

- Complete the numbered steps below in order. Each step is a small, independently reviewable change with its own tests and commit before the next step starts.
- Run the checks relevant to each change and the existing regression suite. Fix failures before committing; record the checks and results in the handoff.
- Keep Docker builds and Compose smoke tests working throughout development. Deployment scaffolding is the first milestone, not a final packaging exercise.
- Pin backend and frontend dependencies, commit the frontend lockfile, and choose explicit runtime image versions in the scaffold. Keep the existing dataset generator dependency and reproducible workbook intact.
- Do not add payroll, currency conversion, approval workflows, or other features outside the approved requirements.

## 2. Architecture and responsibility boundaries

| Component | Owns | Boundary |
| --- | --- | --- |
| FastAPI API in api/ | Authentication, validation, money, employee and band rules, database access, imports, analytics, equity, CSV, migrations | The only component allowed to open SQLite or calculate salary results. |
| Next.js TypeScript web app in web/ | Forms, navigation, directory controls, loading and error states, presentation | Calls the API for all data and mutations. Has no database dependency, connection, volume mount, or duplicated salary rules. |
| SQLite | Persisted employees, bands, migrations, import previews and completion receipts | A file database in WAL mode on the API's named volume. |
| Docker Compose | Exactly two services named api and web, network, health checks, persistent storage | No database service or third runtime service. |

Within the API, keep HTTP routes, domain services, and database queries separate. Share the same employee validator and directory query specification across manual changes, imports, and export. Keep exact-money and statistics functions independently testable.

Use a same-origin /api route on web as a thin forwarding layer to FastAPI at the internal Compose address http://api:8000. It forwards requests, status codes, cookies, file uploads, and downloads without salary processing. The API URL is server-only configuration, not a browser-visible Compose hostname or build-time public variable. The proxy must preserve streaming and error responses, and accommodate the tested 10,000-row upload size and duration.

The API is the security boundary even when accessed directly. Establish one HR sign-in/session flow, protect all employee, band, import, analytics, equity, and export operations, use an HTTP-only session cookie, and protect cookie-authenticated mutations against cross-site requests. Keep deployment credentials and session secrets out of images, source control, and frontend bundles. Use secure cookies under HTTPS for deployment and an explicit local-development setting. Health responses expose no employee data. Do not cache authenticated salary responses across users or serve stale results after mutations.

## 3. Deployment and persistence contract

- Add compose.yaml, separate API and web Dockerfiles and build exclusions, environment examples, and startup instructions in Step 1. Both services must run built applications, not depend on host development servers.
- Mount a named volume, salary_data, at /var/lib/salary in api. Store salary.db and its SQLite -wal and -shm companion files together in that directory. web never mounts it.
- Use a writable local filesystem for the volume on a single deployment host. Start with one API process; SQLite serializes writes, so do not propose multiple hosts sharing this WAL database.
- Initialize and verify WAL mode before readiness. Configure a bounded busy timeout, foreign-key enforcement on each connection, and explicit transaction ownership. Keep write transactions short except where the required atomic import needs one transaction.
- Run ordered schema migrations before accepting requests; persist the schema version. Readiness checks database accessibility and the expected migration version. Compose waits for API health before starting web, and web handles later API outages visibly.
- Test stop/start and container recreation with the named volume retained. Normal restart, rebuild, and Compose down/up must preserve saved data. Document separately that removing the named volume deletes data; never include volume deletion in ordinary startup instructions.
- Document WAL-aware backups using SQLite's backup facilities and verify restore to a separate database. Do not back up only the live main database file while WAL writes are outstanding.
- Add automated backend tests, frontend type checking/lint/build checks, and Compose build/smoke checks early. Tests run as commands or CI jobs against the two services; they do not require adding another persistent Compose service.

These choices follow the documented [SQLite WAL constraints](https://www.sqlite.org/wal.html), [SQLite transaction behavior](https://www.sqlite.org/lang_transaction.html), and [Compose readiness semantics](https://docs.docker.com/compose/how-tos/startup-order/). Keep runtime API configuration server-only following the [Next.js environment-variable rules](https://nextjs.org/docs/app/guides/environment-variables).

## 4. Money and data contracts

### Exact representation

- Store salary and band minimum, midpoint, and maximum as nonnegative SQLite INTEGER minor units. Enforce integer storage type, supported bounds, valid country/level, and positive band midpoint. Reject invalid numeric sizes before database writes.
- Carry saved monetary values through the API as base-10 integer strings explicitly named with a _minor suffix, accompanied by currency. This preserves integer semantics without JavaScript number precision loss. Counts and page numbers may be ordinary JSON integers.
- Forms keep raw monetary input as text. The API alone parses exact decimal strings, rejects negatives before rounding, applies Decimal with ROUND_HALF_UP to two major-unit decimals, and converts to integer minor units. No float, parseFloat, JavaScript Number conversion, or binary floating-point money is permitted anywhere in the flow.
- Return API-produced display strings for monetary statistics and percentages. The frontend displays them without recalculating ratios, medians, rounding, or classifications. API input errors remain authoritative; frontend checks may aid required-field entry only.
- For Excel numeric money cells, read the original decimal XML value into Decimal rather than accepting a workbook library's float conversion. Exact decimal text cells, including all sample workbook money cells, use the same parser. Reject formulas and ambiguous or unsupported numeric cells.
- Use integer accumulation in Python for sums to avoid SQLite aggregate overflow or promotion to REAL. Do not use SQLite AVG or another aggregate that turns money into floating point.

### Fractional results and rounding

Persisted money stays in integer minor units. Derived averages and even-count medians may require fractions of a minor unit; retain their exact integer numerator and denominator until final output. Apply Decimal arithmetic with sufficient precision and ROUND_HALF_UP at the final monetary or percentage display boundary. Do not round a peer median before using it as an equity denominator. Test precision against supported amount and population bounds, rather than relying on an incidental default Decimal context.

For compa-ratio membership, use exact cross-multiplication of integer salary and midpoint against the 80% and 120% boundaries. A rounded display ratio never decides the bucket. Exactly 80% and exactly 120% belong to the middle bucket. Use the [Python Decimal rounding contract](https://docs.python.org/3/library/decimal.html) for parsing and presentation.

### Persistence rules

- Employees retain original text IDs and emails plus normalized uniqueness keys; normalize case and surrounding whitespace consistently. Store a normalized department grouping key and a display name. IDs are immutable.
- Bands have a unique country/level key and ordered integer amounts: minimum ≤ midpoint ≤ maximum, with midpoint > 0. Country supplies currency.
- Do not require an employee-to-band foreign key: missing bands are valid. Derive Band-matched or Unbanded status from the current country/level match using an optional join, not a stored status that can become stale.
- Use database uniqueness and validation constraints in addition to domain validation. Never use replace/upsert or ignore-on-conflict behavior for employee creation or import.
- Use record revisions for employee edits and band mutations to report concurrent-change conflicts instead of silently overwriting changes. Band deletion never cascades to employees.

## 5. API surface and query semantics

Define request/response schemas and structured errors before building each consuming screen. Generate or validate frontend TypeScript contracts from FastAPI's API schema; do not maintain independent salary validation rules in TypeScript.

| API area | Planned operations |
| --- | --- |
| Health and session | Liveness, readiness, HR sign-in, sign-out, session status. |
| Employees | List/search/filter/sort/page, detail, create, revision-checked edit, delete, filter options. |
| Bands | List/detail, create, revision-checked edit, confirmed delete with affected employee count. |
| Directory export | CSV over the complete matching directory query and selected sort. |
| Analytics | Country, department-within-country, and level-within-country salary summaries and compa-ratio counts. |
| Equity | Employee comparisons, states, and reconciliation counts for the selected target population. |
| Imports | Workbook inspection/worksheet selection, preview, preview rows/errors/status, confirm, and completion receipt. |

- Directory search is a trimmed, case-insensitive substring match across ID, name, or email. Combine country, level, and department filters with AND. Escape search wildcard characters so user text remains a literal substring.
- Whitelist sortable fields and directions. Default to employee ID ascending and apply employee ID as a deterministic tie-breaker. Level order is L1–L8. Salary sorting groups country first and sorts salary within country; make the ordering explicit in the response and UI.
- Apply the complete directory query before retrieving a page. Default to 25 and accept only 25, 50, or 100 rows. Return total matching count and page metadata. Count and page results use one consistent read snapshot.
- CSV shares this query and sort but omits the page limit. Export from one consistent snapshot, preserve international text and correct CSV quoting, neutralize spreadsheet formula execution in text cells, and format exact two-decimal money with a separate currency column.
- Analytics filters are explicit country/department/level filters. Do not inherit directory search or page limits. Monetary groups always retain country and currency; never blend currencies.
- Equity filters select reported target employees only. The reference peer population is all eligible saved employees in each target's country and level, regardless of department filters or displayed page.

## 6. Analytics implementation contract

- Load the complete selected salary population in one read snapshot. Ordinary headcounts, averages, medians, min/max, and spread include Unbanded employees. Spread is maximum minus minimum.
- Sort integer salaries to calculate the median. Odd count uses the middle value; even count uses the exact mean of the two middle values. Empty groups expose no-data monetary values; singleton spread is zero.
- Compa-ratio uses the current band midpoint, with exactly three buckets: below 80%, 80 to 120% inclusive, and above 120%. Percentages use eligible Band-matched employees as their denominator. Return eligible, excluded Unbanded, and total counts; handle zero eligible employees explicitly.
- Build sorted peer groups from all Band-matched employees by country and level. Exclude the target by employee identity, removing one occurrence even when salaries tie. Compute the median of the remaining peers and the exact percentage difference from that median.
- One other eligible employee is enough for equity. No other peer means Insufficient peers; a zero peer median is a separate unavailable state. Unbanded, insufficient peers, zero median, and available comparison counts are mutually exclusive and reconcile to the selected target total.
- Avoid one database query or full peer-list sort per employee. Fetch peer groups once, sort each group once, and derive leave-one-out middle positions from the target's position. Test even/odd groups, duplicate salaries, and every exclusion position against a simple independent reference calculation.
- Pagination of an equity display, if needed, affects returned detail rows only. Calculate all summary counts and each target's peer reference from the full appropriate populations before slicing the displayed rows.
- Mutations invalidate affected UI data; begin with uncached authoritative API reads instead of adding a cache invalidation subsystem.

## 7. Import state and transaction contract

### Preview

1. Inspect the .xlsx structure and require worksheet selection when ambiguous. Preserve worksheet names and original Excel row numbers; require the approved headers and reject formulas, corrupt files, or missing/duplicate headers.
2. Parse every nonblank data row with the shared employee and exact-money validators. Continue after row failures. Record all field errors and mark every row participating in an intra-file duplicate, including case-insensitive duplicates.
3. Check current employee uniqueness, derive current band status, and show normalized values, rounding, row totals, valid/invalid counts, and Unbanded counts. Missing bands and salaries outside a band are valid.
4. Persist an immutable normalized preview with its owner, worksheet, source digest, proposed-value digest, errors, relevant band statuses/revisions, a full employee revision/lifetime and band-catalog baseline for confirmation, creation time, and 30-minute expiry. Persist monetary values as integer minor units. Preview persistence changes no employee records.
5. Return an opaque preview ID and explicit clean/invalid status. Every error prevents confirmation. Paging rows/errors never limits validation. Changed files or proposed values require a new preview.

### Confirm

1. Resolve the owned preview; reject missing, invalid, expired, or altered previews. The confirm request references the saved preview rather than supplying replacement employee rows.
2. Acquire SQLite write intent with BEGIN IMMEDIATE before reading the current confirmation state and running confirm-time database checks. If busy beyond the bounded timeout, return a retryable error without employee changes. SQLite permits only one writer; do not check uniqueness outside the transaction and then assume it still holds.
3. Inside that transaction, re-read the preview state and revalidate every proposed employee against current records, database uniqueness rules, and relevant band statuses/revisions. Any employee revision/lifetime or full band-catalog change, a concurrent conflict, or changed reviewed band status invalidates the confirmation and requires a refreshed preview. Missing bands remain valid.
4. Insert every proposed employee using ordinary inserts and mark the preview completed with a durable receipt in the same transaction. Commit once. Roll back all employee inserts and the completion state on any validation, integrity, or operational failure. There are no per-row commits, silent skips, overwrites, or partial success.
5. Serialize simultaneous confirms through the transaction. A retry for a completed preview returns its existing receipt and performs no inserts; it does not make the consumed preview confirmable again. This also resolves a lost success response or API restart after commit.

Persist previews and receipts in the same database volume so restarts do not lose their state. Expired unconfirmed previews may be cleaned up; retain completion receipts for the lifetime of this database in the initial release and never reuse preview IDs. Apply documented upload/decompressed-content limits that allow the committed 10,000-row fixture, and do not execute workbook content or log employee data.

## 8. Ordered implementation steps

### Step 1 — Scaffold both services and prove deployment persistence

**Build:** FastAPI and Next.js TypeScript skeletons; health endpoints; a minimal API-backed web page; the API forwarding layer; both Dockerfiles; Compose with exactly api and web; named volume and WAL initialization; migration entry point; environment examples; single-HR authentication foundation; initial CI and startup instructions. No employee feature is required yet.

**Test before commit:** Build and start both containers from a clean checkout; check readiness through web; verify API authentication boundaries; verify the configured SQLite journal mode is WAL. Create a test-only database sentinel through an API-container verification command, recreate containers with the volume retained, and verify the sentinel survives, then remove the test artifact. Check web has no database mount or database client. Run backend scaffold tests and frontend type/lint/build checks.

**Commit:** build: scaffold api web and persistent compose deployment

### Step 2 — Exact money, employee/band schema, and migrations

**Build:** Shared exact-money parsing/formatting; ordered migrations; employee and band constraints; normalization keys; revisions; repository transaction boundaries; indexes for common filters and identity lookups. Keep integer salaries and optional band matching explicit.

**Test before commit:** Half-up cases including 123.455 → 12,346 minor units; negative-before-rounding rejection; non-finite/float inputs; cent preservation; supported bounds and integer storage; case-insensitive uniqueness; department grouping; valid Unbanded creation; invalid band ordering/zero midpoint; migration upgrade and restart preservation. Use real temporary SQLite files for WAL and constraint tests.

**Commit:** feat: add exact money model and versioned sqlite schema

### Step 3 — Band loading and management API

**Build:** An explicit API-owned command to validate and atomically load the 48 Salary Bands rows from the sample workbook; band CRUD endpoints and revision checks. Initial load requires an empty band catalog; an existing catalog produces an explicit conflict report. Startup never reseeds, replaces, or recreates HR-deleted bands.

**Test before commit:** Verify all 48 unique pairs and their six currencies from the workbook, exact min/mid/max values, duplicate/invalid load rollback, existing-catalog refusal, and CRUD validation. Remove and recreate a band and verify employee preservation and derived band status. Recreate the containers and verify bands persist.

**Commit:** feat: load salary bands and expose band management api

### Step 4 — Employee APIs and CSV export

**Build:** Employee CRUD with immutable IDs and optimistic concurrency, shared search/filter/sort query construction, server-side pagination, filter options, and full-result CSV. Return structured validation/conflict errors and current band status.

**Test before commit:** CRUD and concurrent edit conflicts; duplicate ID/email errors; Unbanded and out-of-band saves; all search fields and combined filters; all sort directions, level ordering, and stable ties; 25/50/100 page behavior and totals; empty results. Load a deterministic test fixture of 10,000 employees through API-owned test setup and prove only the requested page is returned. Verify CSV has every matching row in the same order, Unicode/escaping protection, exact money, header-only empty export, and consistent snapshot behavior.

**Commit:** feat: add employee directory api and filtered csv export

### Step 5 — Working employee directory

**Build:** The first usable directory screen with API-driven CRUD, search, filters, sorting, 25/50/100 server pagination, count/range display, delete confirmation, field errors, country-change salary confirmation, and CSV download. Keep this a functional vertical slice; finish the remaining screens and polish in Steps 10–12.

**Test before commit:** Browser tests through Compose for creation/edit/delete, duplicate/conflict errors, filter reset and page reset, last-page deletion, empty/loading/failure states, export parity, and persisted records after restart. Verify the frontend requests pages rather than fetching the entire directory and performs no salary calculation.

**Commit:** feat: build api-backed employee directory

### Step 6 — Salary summaries and compa-ratio API

**Build:** Full-filtered-population country, department-within-country, and level-within-country statistics and exact compa-ratio buckets, with explicit eligibility counts and empty states.

**Test before commit:** Odd/even medians including 100.00 and 100.01 → displayed 100.01; exact average and spread; empty/singleton groups; mixed currencies; all-Unbanded and mixed eligibility. Test exactly 80%/120% and 79.999% displayed as 80.00% but classified below. Verify filter totals across more than one directory page and reconcile all bucket counts.

**Commit:** feat: add exact salary analytics and compa-ratio api

### Step 7 — Equity API

**Build:** Leave-one-out peer medians, exact percentage differences, full-reference peer groups, availability states, and target-population reconciliation counts.

**Test before commit:** Singleton and two-person groups; even/odd peer counts; tied salaries and exclusion of one identity; zero peer median; Unbanded exclusions; the requirements' 100/200/400 example. Verify a department filter changes reported targets without shrinking their country/level peers. Compare optimized results with an independent reference implementation on deterministic cases and the 10,000-row dataset; check no per-employee database query pattern.

**Commit:** feat: add peer median equity analysis api

### Step 8 — Excel inspection and complete preview

**Build:** Workbook inspection and sheet selection; exact cell parsing; all-row validation; durable immutable previews; paged preview/error retrieval; clean/invalid state, totals, source Excel row numbers, and expiry. Do not expose confirmation until Step 9 is complete.

**Test before commit:** Preview the full sample workbook and verify 10,000 valid employees and zero employee writes. Test multiple sheets, reordered/missing/duplicate headers, corrupt/empty workbooks, partially blank rows, multiple errors per row, duplicate IDs/emails marking every participant, existing-record conflicts, formulas, leading-zero IDs, raw numeric decimal cells, rounding, missing bands, expiry, and all errors beyond the first preview page.

**Commit:** feat: add validated durable excel import previews

### Step 9 — Atomic, current-database import confirmation

**Build:** The write transaction and revalidation protocol in Section 7, including durable completion receipts, busy/conflict responses, and retry behavior.

**Test before commit:** Import all 10,000 sample employees into an empty employee database and independently verify country counts 4,000/2,500/1,200/1,000/800/500 and band-position counts 9,500/250/250. Introduce a duplicate after clean preview; change a relevant band; force a failure after some insert statements; submit concurrent confirms; retry after a lost response; restart the API after completion. Every failed first attempt adds zero employees, every success creates the entire preview once, and no existing employee is skipped or overwritten. Verify stale/invalid/expired previews cannot confirm and valid Unbanded rows can confirm. Use separate connections against real file databases for concurrency tests.

**Commit:** feat: confirm excel imports atomically with retry-safe receipts

### Step 10 — Band and import UI

**Build:** Band list/forms/deletion impact, workbook selection/upload, preview rows and all error pages, totals, Unbanded notices, clean-only confirmation, expiry/conflict recovery, and completion receipts. Preserve the selected file and user criteria where recovery allows; require a new preview for corrected values.

**Test before commit:** Browser flows through Compose for valid and invalid imports, errors at original Excel row numbers, disabled confirm for any invalid preview, backend rejection of bypassed controls, stale preview rejection, retry without duplicate employees, and band deletion/recreation effects. Verify uploads and download/proxy behavior at full fixture size.

**Commit:** feat: add salary band and two-phase import screens

### Step 11 — Analytics/equity UI and interaction polish

**Build:** API-backed summary and equity screens, their own country/department/level filters, currency labels, all three compa-ratio buckets, exclusion counts, insufficient/zero-peer states, navigation, and accessible loading/empty/error experiences. Refresh affected views after employee, band, or import mutations. Explain that equity peers span the full country/level group.

**Test before commit:** Browser checks for filter behavior and full-population totals, cross-currency separation, Unbanded reconciliation, equity states, and data refresh after mutations. Keyboard-test core forms, confirmations, tables, and error focus. Verify displayed API rounding is preserved and that no frontend aggregation is introduced.

**Commit:** feat: complete salary analytics and equity interface

### Step 12 — Full-system acceptance and deployment rehearsal

**Build:** Finish runbooks for setup, migration, explicit band loading, sample preview/confirm, backup/restore, configuration, and upgrade/restart operations. Add an acceptance trace mapping every scenario in REQUIREMENTS.md Section 12 to a passing automated test or documented browser check. Make only fixes demonstrated by these checks.

**Test before commit:** Rebuild both images; start with a fresh disposable volume; load bands; import the complete sample through preview/confirm; verify directory, CSV, analytics, and equity against an independent exact calculation. Verify existing employee/band/preview/receipt persistence across down/up and container recreation. Restore a WAL-aware backup into a separate volume and compare counts and exact amounts. Exercise API outage recovery, unauthorized access rejection, and all required regression, frontend, and browser checks. Record timings for 10,000-row directory queries, analytics, equity, preview, and confirm to expose slow operations without inventing an unapproved SLA.

**Commit:** test: verify full salary workflows and persistent deployment

## 9. Completion criteria

The build is complete when every step has its tested commit, all required acceptance scenarios pass, Compose starts exactly api and web from documented commands, and the named-volume persistence and restore rehearsals succeed. The sample fixture remains reproducible and unchanged. FastAPI owns every salary rule and database interaction; Next.js renders API results. No feature uses a directory page as its analytics population, floating-point money, partial import commits, or silent conflict handling.
