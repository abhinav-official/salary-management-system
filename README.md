# Salary management system

FastAPI owns authentication, SQLite, and all salary logic. The Next.js TypeScript app calls the API through a same-origin proxy. The API provides service health, single-HR sign-in, and salary band management; employee features follow [the build plan](docs/PLAN.md).

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

Run deployment and band acceptance checks from the repository root:

```sh
python3 tools/smoke_compose.py
```

It creates an isolated project, temporary credentials, and a new named volume; performs an uncached Compose build; and verifies health through web, sign-in and CSRF, WAL, and absence of web mounts. It explicitly loads the workbook's 48 bands, checks conflict refusal, deletes a band with an affected test employee, and proves startup leaves it deleted after both containers are recreated. It recreates the band, checks the employee's derived status and unchanged data, recreates the containers again, and verifies the complete catalog persists. It also verifies sentinel/session preservation and outage recovery, removes its test employee and sentinel, stops its containers, and prints the retained test-volume name. It never replaces .env or removes volumes. CI runs both backend tests against the read-only sample workbook and this deployment check.

## Project documents and data

- [Requirements](docs/REQUIREMENTS.md)
- [Build plan](docs/PLAN.md)
- [Sample data and regeneration](data/README.md)
