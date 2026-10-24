# Salary management system

FastAPI owns authentication, SQLite, and all salary logic. The Next.js TypeScript app calls the API through a same-origin proxy. The API provides service health, single-HR sign-in, salary band management, employee CRUD, directory queries, CSV export, salary summaries, and compa-ratio analytics. The working employee directory supports search, filters, sorting, pagination, forms, and CSV download. Equity, imports, and the remaining screens follow in [the build plan](docs/PLAN.md).

## Start with Docker

Requires Docker with Compose and Python 3.10+ for the password setup helper.

```sh
cp .env.example .env
python3 tools/hash_password.py >> .env
docker compose build
docker compose up -d --wait
```

The helper prompts for and confirms a password, then writes only its salted scrypt hash. Set HR_EMAIL in .env to the HR account you want. Open [the local workspace](http://localhost:3000) and use that email and password. No default password is included. Keep the generated hash single-quoted in .env so Compose does not interpolate its dollar signs.

Compose starts exactly two services, api and web. Only web publishes a loopback port. Browser requests to /api are forwarded to http://api:8000 using server-only runtime configuration. The frontend has no SQLite package or volume mount. Both containers run as non-root users.

For an HTTPS deployment, set PUBLIC_ORIGIN to the exact public origin, set COOKIE_SECURE=true, and terminate HTTPS in your host ingress in front of web. The shipped port is loopback-only for local use or a same-host ingress. PUBLIC_ORIGIN has no trailing slash or path and must match the browser origin exactly. Credentials and database configuration are supplied to api only; web receives only its internal API URL. Keep .env out of source control. HR sessions expire after eight hours, survive service restarts, and are revoked on sign-out. Sign-in checks the Origin header; authenticated mutations additionally require the session's CSRF token.

## Health, migrations, and persistence

- GET /api/health/live reports process liveness through web.
- GET /api/health/ready verifies SQLite accessibility, WAL mode, and the expected schema version through web. Health responses contain no employee or credential data.
- API startup runs python -m app.migrate before starting one Uvicorn process. Ordered SQL migrations and SQLite user_version are committed together in a write transaction. Re-running migrations preserves existing data; a newer database is rejected instead of downgraded.
- salary_data is a named volume mounted at /var/lib/salary only in api. salary.db, salary.db-wal, and salary.db-shm share that directory. Every connection enables foreign keys and a five-second busy timeout; migrations enable WAL. This deployment uses a local volume on a single host.

Normal shutdown and restart retain data:

```sh
docker compose down
docker compose up -d --wait
```

Container recreation also preserves the volume:

```sh
docker compose up -d --force-recreate --wait
```

Removing the named volume deletes its database. Do not add the volume-removal option to ordinary shutdown commands. Never copy only the main file of a live WAL database as a backup; use SQLite's online backup API. Full backup/restore acceptance is scheduled in the plan's final deployment rehearsal.

## Exact money and schema foundations

Schema version 2 adds employees and salary_bands while preserving version 1's sessions. Startup applies ordered migrations atomically and preserves records on repeated starts.

Money enters the shared API domain code as plain decimal text or an exact Decimal, rounds with ROUND_HALF_UP, and becomes integer minor units. Negative inputs are rejected before rounding, including negative sub-cent values. Binary floats, booleans, non-finite values, currency symbols, and thousands separators are rejected. The supported range is 0 through 9,223,372,036,854,775,807 minor units; formatting returns exact two-decimal text without separators. Derived analytics retain fractional results until presentation when those features are added.

Employee IDs, names, emails, and departments retain trimmed display text. SQLite derives stored Unicode case-folded identity, email, name, and department keys; identity and email keys are unique. All API database access must use app.database.connect so its deterministic normalization functions are registered. IDs are immutable, revisions start at 1 by default, and updates must increment the revision exactly once; mutation services must also match the caller's expected revision in their update predicate.

Employee salary and band amounts use constrained INTEGER columns in STRICT tables. Bands are unique by country/level, require minimum ≤ midpoint ≤ maximum and midpoint > 0, and derive currency from country. Employees have no band foreign key: missing or deleted bands preserve employees and their salaries. The shared transaction context rolls back a whole batch on any failure.

## Load and manage salary bands

After API startup has applied migrations, explicitly load the sample catalog:

```sh
docker compose cp data/employees_10000.xlsx api:/tmp/employees_10000.xlsx
docker compose exec -T api python -m app.load_bands /tmp/employees_10000.xlsx
```

The API-owned command reads only Salary Bands, validates all 48 country/level pairs and their currencies, and inserts the entire catalog in one transaction. It reads money as exact decimal text or original numeric XML values, rejects formulas, and reports validation errors with worksheet and Excel row numbers. It creates no employees. Success prints JSON with created=48 and the saved catalog; failure exits with status 1 and a JSON error on stderr. An existing catalog, even one band, returns band_catalog_not_empty with every existing key and revision and changes nothing. A validation or insert failure saves no bands. Startup only migrates the schema; it never invokes this loader, fills missing bands, or recreates HR-deleted bands.

The authenticated API is available through web at /api/bands (direct API paths omit /api):

| Method and path | Contract |
| --- | --- |
| GET /api/bands | Full catalog, ordered by country and level, with total count. |
| GET /api/bands/{country}/{level} | Band, affected employee count, and ETag response header. |
| POST /api/bands | Create a new country/level band; duplicates return 409. |
| PUT /api/bands/{country}/{level} | Replace its three amounts using the current If-Match token. |
| DELETE /api/bands/{country}/{level}?confirmed=true | Delete using the current If-Match token and explicit confirmation. Reports affected employees; preserves their records and salaries. |

Create bodies contain country, level, minimum_amount, midpoint_amount, and maximum_amount. Edit bodies contain only the three amount fields. Amount inputs are decimal strings in major units, for example "123.455"; the API applies shared half-up validation. Responses provide exact integer strings in minimum_minor, midpoint_minor, and maximum_minor, two-decimal *_amount strings, derived currency, revision, and an opaque etag. Country and level identify the band; changing the pair uses explicit delete/create operations.

All mutations require the trusted Origin and the session's X-CSRF-Token, as for sign-out. Copy the exact ETag returned by detail/create/edit (also present in list items) into If-Match for edit or delete. Missing If-Match returns 428; a stale token or wildcard returns 412. Missing deletion confirmation returns 400 with the affected employee count and Unbanded warning. Invalid values return 422; a missing band returns 404. API responses use no-store.

Schema version 3 adds a persisted identity for each band lifetime without changing existing band amounts or revisions. Its ETag combines identity and revision, so a stale request cannot change a deleted-and-recreated band even when both lifetimes start at revision 1. Only this identity metadata cascades on deletion; employees have no foreign key to either band table. Band status is derived from the current country/level match, so deleting/recreating a band changes status without changing employee data.

## Employee directory API and CSV export

These authenticated endpoints are available through web; direct API paths omit /api:

| Method and path | Contract |
| --- | --- |
| GET /api/employees | Filtered, sorted page with items, total, page, page_size, total_pages, and ordering. |
| GET /api/employees/filter-options | Six supported countries, eight levels, and current departments grouped by trimmed, case-folded key. Department options have a value key and deterministic display label. |
| GET /api/employees/export.csv | Complete matching directory, in the same order, as an attached UTF-8 CSV. |
| GET /api/employees/records/{employee_id} | Current employee, derived band status, and ETag response header. |
| POST /api/employees | Create; duplicate ID/email returns 409 with field-specific conflicts. |
| PUT /api/employees/records/{employee_id} | Replace mutable fields using the current If-Match token. |
| DELETE /api/employees/records/{employee_id}?confirmed=true | Delete with current If-Match token and explicit confirmation; return the deleted employee snapshot. |

Create bodies contain employee_id, name, email, department, country, level, and salary_amount. Edit bodies contain all these fields except the immutable employee_id. Text is trimmed, display spelling is retained, and ID/email uniqueness is Unicode case-insensitive. salary_amount must be plain decimal text; exact shared half-up parsing applies. Responses contain salary_minor as an integer string, salary_amount as exact two-decimal text, country-derived currency, revision, opaque etag, and current band_status (Band-matched or Unbanded). Missing bands, zero salaries, and salaries outside a band are valid. The records path keeps employee IDs distinct from directory utility routes; URL-encode IDs when constructing a request.

Edit and delete use the same Origin, X-CSRF-Token, and If-Match conventions as bands. A country change also requires ?country_change_confirmed=true and an explicit salary_amount in the new currency; the API performs no currency conversion. Missing confirmation returns 400, missing If-Match returns 428, and stale revisions return 412 with the current employee and token. Invalid fields return structured 422 validation details; uniqueness conflicts return 409 with detail.errors entries naming employee_id and/or email. Missing employees return 404. Failed edits leave all fields unchanged.

Directory and export query parameters:

| Parameter | Values and behavior |
| --- | --- |
| search | Trimmed, Unicode case-insensitive literal substring across ID, name, or email. Percent, underscore, and backslash are literal characters. |
| country, level, department | Combined with AND and search. Country and level use their supported labels; department uses a trimmed, case-folded comparison. Empty filters mean all. |
| sort | employee_id (default), name, email, department, country, level, or salary. |
| direction | asc (default) or desc. Level sorts L1 through L8; non-ID ties always use employee ID ascending. Salary always groups country ascending first, then local salary in the selected direction, then ID ascending; ordering describes this explicitly. |
| page, page_size | Positive page number, default 1; sizes 25 (default), 50, or 100 only. Export accepts these parameters but ignores pagination. |

Count and page retrieval share one SQLite read snapshot, and only the requested page is selected. An empty result has items=[], total=0, and total_pages=0; an out-of-range page is empty while retaining its matching total. Filter options cover the full directory rather than a page or current search.

CSV columns are Employee ID, Name, Email, Department, Level, Country, Currency, Annual Salary, and Band Status. Export shares the directory query, removes its limit, and finishes one consistent snapshot before sending a successful download. Unicode, commas, quotes, and embedded newlines are preserved with CSV quoting. Text cells beginning with formula characters (=, +, -, @), including behind whitespace, or leading tab/CR/LF receive an apostrophe prefix. Salaries retain exactly two decimals with a separate currency column. Empty exports contain the header only.

Schema version 4 adds persisted employee lifetime identities, backfilling existing rows without changing their fields or revisions. Tokens survive restart; deleting and recreating the same ID creates a new token so requests from its old lifetime fail. Only employee identity metadata cascades when deleting an employee.

## Salary summaries and compa-ratio API

Both endpoints require the HR session and return no-store responses through web:

| Path | Response |
| --- | --- |
| GET /api/analytics/summaries | filters, total_count, and countries, departments, and levels group arrays. |
| GET /api/analytics/compa-ratio | filters, total_count, eligible_count, excluded_unbanded_count, state, and exactly three buckets. |

Use country, department, and level query parameters, combined with AND. Country and level use supported labels; surrounding whitespace is trimmed, departments compare by Unicode case-folded key, and blank filters mean all. The returned filters object identifies the active filters, with department normalized to its key. Analytics has an independent full filtered population: directory search, page, page_size, sort, and unknown query parameters return 422. It never slices a directory page.

Every salary group has country, currency, count, state, average, median, minimum, maximum, and spread. Country groups cover all six supported countries, or the selected country. Department groups cover each known saved department in each selected country; the department filter narrows this to its key, including an explicit empty group if the key is unknown. Labels use the same deterministic spelling as directory filter options. Level groups cover L1–L8 within each selected country, or only the selected level. Countries sort alphabetically, departments by normalized key, and levels L1–L8. With no known departments and no department filter, the department array is empty.

Unbanded employees participate in all these headcounts and statistics. Each grouping's counts sum to total_count. Countries and currencies always remain separate; the only overall total is a headcount. Empty groups have count=0, state=no_data, and all five monetary statistics null. Nonempty groups have state=available. A singleton has zero spread. Spread is maximum minus minimum.

Each non-null statistic contains minor_numerator and minor_denominator as exact integer strings, plus amount as a two-decimal major-unit display string. For salaries 100.00 and 100.01, both average and median are {"minor_numerator":"20001","minor_denominator":"2","amount":"100.01"}. Integer accumulation and rational intermediates preserve fractional minor units; Decimal half-up rounding occurs only at display, using a private precision context derived from the fraction's digit bounds. Salaries are not accumulated using SQLite SUM/AVG or binary floats.

Compa-ratio buckets are always below_80 (Below 80%), 80_to_120 (80 to 120%, inclusive), and above_120 (Above 120%), in that order. Membership compares salary_minor × 100 with midpoint_minor × 80 and midpoint_minor × 120 using integers. Exactly 80% and 120% are in the middle; 79.999% remains below even though its two-decimal display is 80.00. Current matching bands determine eligibility, including salaries outside their band range. Missing bands contribute only to excluded_unbanded_count.

Every bucket contains key, label, count, and percentage, a two-decimal string using eligible_count as denominator. Bucket counts sum to eligible_count, and eligible_count plus excluded_unbanded_count equals total_count. Percentages round independently and may sum to 100.01. With zero eligible employees, state=no_eligible_data, all bucket counts are zero, and percentages are null; otherwise state=available. Each response loads its complete employee population and current band midpoints in one WAL read snapshot. Summary department labels share that snapshot. Subsequent reads recompute after employee or band changes.

## Use the employee directory

After signing in, use the search box for employee ID, name, or email. Country, level, and department filters combine with search; changing them resets to page 1. Column headings toggle sorting and show its direction. Salary ordering is described above the table. Choose 25, 50, or 100 rows per page; the footer shows the matching range, total, and page. Clear filters resets search and all three filters.

Add employee opens a form. Edit keeps the employee ID read-only. Salary inputs remain decimal text, and saved values, currencies, and band status come directly from the API. API validation and duplicate conflicts appear beside their fields without discarding entered values. Changing country requires confirming the entered salary in that country's currency context before saving. Stale edit/delete attempts show a conflict and require an explicit reload and review before retrying; they never silently overwrite another change.

Delete requires a confirmation dialog. Deleting the final row on a page returns to the preceding available page. Download CSV exports every match in the current search/filter/sort order, independently of pagination. Loading, empty, directory failure, filter-option failure, and download failure states are explicit; failures offer retry controls. The browser requests only the selected directory page and makes no salary calculations or band determinations. Data access remains entirely in the API.

## Checks

Backend tests use temporary file databases and real FastAPI requests:

```sh
docker build --target test -t salary-api-tests api
docker run --rm --mount "type=bind,source=$PWD/data,target=/fixtures,readonly" -e BAND_SAMPLE_WORKBOOK=/fixtures/employees_10000.xlsx salary-api-tests
```

Frontend dependency installation, type checking, linting, and production compilation:

```sh
cd web
npm ci
npm run typecheck
npm run lint
npm run build
```

The web Docker build runs the same type/lint/build gates. The backend pins all runtime dependencies in api/requirements.txt, test additions in api/requirements-dev.txt, and the frontend commits an npm lockfile. Root requirements.txt remains dedicated to the dataset generator.

Run deployment, band, and employee acceptance checks from the repository root:

```sh
python3 tools/smoke_compose.py
```

It creates an isolated project, temporary credentials, and a new named volume; performs an uncached Compose build; and verifies health through web, sign-in and CSRF, WAL, and absence of web mounts. It explicitly loads the workbook's 48 bands, checks conflict refusal, deletes a band with an affected test employee, and proves startup leaves it deleted after both containers are recreated. It recreates the band, checks the employee's derived status and unchanged data, recreates the containers again, and verifies the complete catalog persists. Through the web proxy it also checks employee CRUD, stale edits, combined directory filters and sorting, exact CSV content/headers, and employee persistence across container recreation. Analytics checks verify exact summaries, currency separation, filtered counts, Unbanded eligibility reconciliation, no-eligible states, and results after recreation and deletion. It verifies sentinel/session preservation and outage recovery, removes its test employees and sentinel, stops its containers, and prints the retained test-volume name. It never replaces .env or removes volumes. CI runs both backend tests against the read-only sample workbook and this deployment check.

Run real Chromium acceptance tests through an isolated Compose deployment:

```sh
cd web
npm ci
npx playwright install --with-deps chromium
cd ..
python3 tools/browser_compose.py
```

This builds api/web, seeds a deterministic 10,000-employee fixture through API-owned domain/repository code, and tests the actual sign-in and directory UI through web. Tests cover CRUD, field errors, duplicate/conflicting edits and deletes, country confirmation, search/filter/sort/page resets, all page sizes, last-page deletion, loading/empty/failure/retry states, stale search responses, CSV byte parity with the filtered API, and direct rendering of API salary/status values. It recreates both containers and runs a second browser check for the record created before restart. Browser tests are pinned in web/package-lock.json and run as a separate CI job.

The runner never reads or replaces .env. It generates test credentials and a fresh named volume, stops its containers on exit, and retains that volume. Browser traces/screenshots are ignored by git and Docker builds; they may contain test credentials. Use --keep-running for manual inspection; it prints the private state file path needed to stop that isolated project afterward.

## Project documents and data

- [Requirements](docs/REQUIREMENTS.md)
- [Build plan](docs/PLAN.md)
- [Sample data and regeneration](data/README.md)
