# Salary management system

FastAPI owns authentication, SQLite, and all future salary logic. The Next.js TypeScript app calls the API through a same-origin proxy. This first build provides service health and single-HR sign-in; employee features follow [the build plan](docs/PLAN.md).

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

## Checks

Backend tests use temporary file databases and real FastAPI requests:

```sh
docker build --target test -t salary-api-tests api
docker run --rm salary-api-tests
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

Run the Step 1 deployment acceptance test from the repository root:

```sh
python3 tools/smoke_compose.py
```

It creates an isolated project, temporary credentials, and a new named volume; performs an uncached Compose build; verifies health through web, sign-in and CSRF, WAL, absence of web mounts, and a sentinel and session surviving down/up with both containers recreated. It removes the sentinel, verifies outage recovery, stops its containers, and prints the retained test-volume name. It never replaces .env or removes volumes. CI runs both backend tests and this deployment check.

## Project documents and data

- [Requirements](docs/REQUIREMENTS.md)
- [Build plan](docs/PLAN.md)
- [Sample data and regeneration](data/README.md)
