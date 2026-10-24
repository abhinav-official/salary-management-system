# Salary management operations

Run commands from the repository root. Docker with Compose and Python 3.10+ are required. The deployment has exactly two services, `api` and `web`, on one host. Only `api` mounts the local named SQLite volume. The frontend reaches the API through `/api`; it has no database credentials or access. See [acceptance evidence](ACCEPTANCE.md) and [recorded results](ACCEPTANCE_RESULTS.md).

## Setup and configuration

```sh
cp .env.example .env
chmod 600 .env
docker compose build
docker compose up -d --wait --wait-timeout 180
curl --fail http://localhost:3000/api/health/ready
```

Set the origin in `.env` before starting. With both HR credentials omitted and no saved account, startup seeds `demo@acme.demo` / `Demo-2026-Salary` using a salted scrypt hash. For a private account, set `HR_EMAIL` and append the hash from `python3 tools/hash_password.py >> .env` before first startup. Keep the original configured credentials on the first upgrade from schema 6 so that account is persisted instead of replaced with demo credentials. Once saved, the HR account is never changed by startup or environment changes. Open the configured origin, sign in, and check API health. No bands or employees are seeded at startup. Keep the project name consistent: changing Compose's project name selects a different named volume. Use `docker compose config --services` to verify `api` and `web`; avoid sharing full resolved configuration because it includes secrets.

| Setting | Meaning |
| --- | --- |
| WEB_PORT | Host loopback port, default 3000. |
| PUBLIC_ORIGIN | Exact browser origin, including scheme and nonstandard port; no path. Login and mutations enforce this value. |
| COOKIE_SECURE | `false` only for local HTTP; `true` for HTTPS. Other values fail startup. |
| HR_EMAIL | Optional first-start single HR identity, normalized case-insensitively. Must accompany HR_PASSWORD_HASH; ignored for replacement of a saved account. |
| HR_PASSWORD_HASH | Optional first-start password helper output, quoted as generated; must accompany HR_EMAIL. A saved hash is never replaced at startup. |
| DATABASE_PATH | API-container path `/var/lib/salary/salary.db`, supplied by Compose. Keep database and WAL/SHM siblings on the same volume. |
| API_BASE_URL | Internal web-to-api URL `http://api:8000`, supplied by Compose. Never expose the API directly. |

For HTTPS, configure an external TLS reverse proxy to the web loopback port, set `PUBLIC_ORIGIN` to its HTTPS origin and `COOKIE_SECURE=true`. Compose itself does not terminate TLS. Do not publish this local HTTP configuration as an Internet service. Sessions last eight hours and survive restarts; sign-out revokes them. All mutations require both the trusted Origin and session CSRF token. Keep `.env`, backups, cookies, receipts and preview contents private; database backups include personal salary data and persisted sessions.

## Migrations and readiness

API startup executes `python -m app.migrate` before accepting requests. Ordered migrations commit schema changes and `PRAGMA user_version` in one transaction; repeated execution preserves records. A newer database is rejected rather than downgraded. To apply migrations explicitly during a maintenance window, stop the API first:

```sh
docker compose stop api
docker compose run --rm --no-deps api python -m app.migrate
docker compose up -d --wait --wait-timeout 180
```

Verify liveness at `/api/health/live` and database/WAL/schema readiness at `/api/health/ready`. During API downtime the proxy returns 503; after recovery use the UI Retry control. Inspect failures with `docker compose logs --tail 100 api web`; do not delete the volume to repair a migration error. Back up before upgrading. The current schema is version 7; it adds the persisted single HR account. The API initializes that account after migration and before serving requests. Migrations never load or recreate salary bands.

## Explicit band loading

For a new, empty catalog only:

```sh
docker compose cp data/employees_10000.xlsx api:/tmp/employees_10000.xlsx
docker compose exec -T api python -m app.load_bands /tmp/employees_10000.xlsx
```

Expect `created: 48`, six currencies, and no employees. The command validates every Salary Bands row, then atomically inserts all 48 pairs. Any existing band causes an explicit `band_catalog_not_empty` conflict (exit 1), including keys and revisions; validation and storage errors also add zero bands. Do not clear a populated catalog to make the command succeed. Use the signed-in Salary Bands screen to manage it. Deletion names the affected employees and makes them Unbanded without deleting their records; creation restores derived eligibility. Startup does not reseed deleted bands.

## Sample preview and confirmation

Sign in and open Excel import. Choose `data/employees_10000.xlsx`, inspect the workbook, and explicitly select **Employees** from the three worksheets. Preview validates all rows and stores a 30-minute immutable snapshot. Expect 10,000 valid, zero invalid and zero Unbanded with the full catalog. Preview creates zero employees. Review row/error pages, normalized money, source Excel row numbers, expiry and totals before confirming.

Confirm only a clean preview. Expect a durable receipt containing preview ID, actor, completion time, inserted count 10,000 and ordered employee IDs. Confirm rechecks the complete current catalog and employee revisions inside one write transaction. A duplicate or stale state creates zero employees; correct the source or current conflict and create a fresh preview. No row is silently overwritten or skipped. Unbanded and out-of-band salaries are valid; a missing band only excludes that employee from compa-ratio/equity.

If a response is lost or the API restarts, retry **the same preview ID** or retrieve its receipt; completed confirmation returns the original receipt without inserting again, including after preview expiry. A retryable busy/503 response is safe to retry with that ID. Invalid/expired uncompleted previews require a fresh preview. A foreign preview is inaccessible. Do not upload again merely because a completed receipt response was lost. Reimporting the sample into a populated database reports existing-record conflicts.

Verify Directory total 10,000, page sizes 25/50/100, and full CSV download; summary and equity filters operate over the full population. Country totals: India 4,000; US 2,500; UK 1,200; Germany 1,000; Singapore 800; UAE 500. Positions: 9,500 inside, 250 below, 250 above. The fixture has exact decimal text amounts and seed 42; see [dataset documentation](../data/README.md).

## Online backup (WAL-aware)

Use SQLite's online backup API while the API is running. Copying only `salary.db` can lose committed WAL changes. This produces a consistent database snapshot, including immutable previews and durable receipts; transactions committed after that snapshot are outside the backup.

```sh
mkdir -p backups
chmod 700 backups
docker compose exec -T api python - <<'PY'
import os
import sqlite3
source = sqlite3.connect(os.environ['DATABASE_PATH'])
target = sqlite3.connect('/tmp/salary-backup.db')
try:
    source.backup(target)
finally:
    target.close()
    source.close()
PY
docker compose cp api:/tmp/salary-backup.db backups/salary-backup.db
chmod 600 backups/salary-backup.db
docker compose exec -T api python -c "from pathlib import Path; Path('/tmp/salary-backup.db').unlink()"
```

Use a distinct dated filename for retained copies; the example replaces its previous snapshot. Store encrypted backups outside the Docker host according to the operator's retention policy, and rehearse restore before relying on them. `backups/` is ignored by Git. A successful file copy alone is not a restore check.

## Restore into a separate volume

Restore with the target API stopped, into a **new project name and fresh volume**. Never copy a backup over an active SQLite database or leave stale WAL/SHM files beside a replacement. The following uses a different web port so the source deployment can remain running:

```sh
cp .env .env.restore
chmod 600 .env.restore
```

Edit `.env.restore`: set `WEB_PORT=3001` and `PUBLIC_ORIGIN=http://localhost:3001` (or the actual recovery origin). The restored HR account and its preview ownership are retained; environment credentials do not replace it. Use images from the backup's schema-compatible release. Choose a project name not already in use, then:

```sh
docker compose --env-file .env.restore -p salary-restore build
docker compose --env-file .env.restore -p salary-restore run --rm --no-deps -T api python -c '
import os
import sqlite3
import sys
from pathlib import Path
path = Path(os.environ["DATABASE_PATH"])
if path.exists():
    raise SystemExit("Restore requires a fresh target volume")
Path("/tmp/backup.db").write_bytes(sys.stdin.buffer.read())
source = sqlite3.connect("file:/tmp/backup.db?mode=ro", uri=True)
target = sqlite3.connect(path)
try:
    source.backup(target)
finally:
    target.close()
    source.close()
' < backups/salary-backup.db
docker compose --env-file .env.restore -p salary-restore up -d --wait --wait-timeout 180
curl --fail http://localhost:3001/api/health/ready
```

The backup is streamed over stdin so its private host permissions work with the nonroot API container. The temporary copy is removed with the one-off container. Sign in at the recovery origin; compare directory totals and CSV exact amounts, all 48 bands, analytics currency groups, preview rows and completion receipts with the source snapshot. The automated rehearsal also compares content digests for every table and proves a committed record still in WAL survives backup. Backups are not portable downgrades; upgrade through ordered migrations if needed. Cutover routing and decommissioning the source are separate operator actions after validation.

## Upgrade, restart and recovery

Record the current Git commit/image IDs and take an online backup first. During a planned maintenance window, stop traffic/mutations, check out the reviewed release, rebuild both services and start:

```sh
docker compose stop
docker compose build
docker compose up -d --wait --wait-timeout 180
```

Verify readiness, sign-in, directory/CSV totals and a known receipt. Existing volume contents migrate before API readiness. For an ordinary restart use `docker compose restart` then check readiness. For shutdown/recreation:

```sh
docker compose down
docker compose up -d --force-recreate --wait --wait-timeout 180
```

Both preserve the named volume. Never use `down -v` for ordinary operations. Do not roll an older application against a newer schema. If an upgrade fails, retain the source volume and logs; restore the pre-upgrade backup to a separate volume with its compatible previous release and validate before routing traffic. Origin changes require recreation (`up -d --force-recreate`), not just restart. HR environment variables initialize an empty account catalog only; changing them does not reset a saved password or identity. There is no account-management/password-reset endpoint. No destructive session or preview cleanup is automated.

## Rehearse and run regressions

These commands provision disposable credentials and uniquely named volumes and never read local `.env`. Containers stop on exit; test volumes remain for inspection. Output contains no passwords; browser artifacts and database volumes still contain fixture/session data.

```sh
python3 tools/acceptance_compose.py --report /tmp/salary-acceptance.json
python3 tools/smoke_compose.py
python3 tools/browser_compose.py
docker build --target test -t salary-api-tests api
docker run --rm --mount "type=bind,source=$PWD/data,target=/fixtures,readonly" \
  -e BAND_SAMPLE_WORKBOOK=/fixtures/employees_10000.xlsx salary-api-tests
```

The web image runs TypeScript, ESLint and the Next.js production build. The browser runner uses the pinned Playwright installation (`cd web`, `npm ci`, `npx playwright install chromium` on a new host). Record fresh timings from the acceptance report; these are observations, not an approved latency SLA. Preserve the report with the release evidence. Remove only explicitly identified disposable volumes after inspection, never the operational `salary_data` volume.
