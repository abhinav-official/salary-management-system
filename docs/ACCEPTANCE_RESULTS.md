# Step 12 acceptance and deployment results

Verified on 24 October 2026. Rehearsal started at 2026-10-24T06:59:28.789410+00:00. Host: macOS arm64, Docker Desktop engine 29.9.2, Compose 5.6.1, 8 allocated CPUs and 6,210,043,904 bytes of Docker memory. Runtime images: Python 3.14.8 and Node 24.14.0; Next.js 16.4.3. This is a local shared host, not an isolated performance laboratory.

## Result

**PASS:** all 20 [requirements section 12 scenarios](ACCEPTANCE.md) have passing automated evidence. No application behavior fixes were needed. Changes add operational documentation, an independent acceptance oracle, deployment/restore automation, CI execution and two precise acceptance regressions (Excel rows 7/12 and failure after 5,000 inserts in a 10,000-row confirmation).

| Check | Outcome |
| --- | --- |
| Host backend regression | 176 passed, 67.357 seconds. |
| Container backend regression | 176 passed, 144.683 seconds, including both new acceptance tests. |
| Frontend gates | TypeScript, ESLint and Next.js production build passed in rebuilt web image. |
| Browser through Compose | 32 passed: 12 directory, 8 band/import, 9 analytics/equity, 1 full sample import, 2 restart checks. |
| Deployment smoke regression | Passed all 14 reported checkpoints, including auth/CSRF, exact money, band lifetime/reseed behavior, stale preview, persistence, outage and idempotent receipt recovery. |
| Independent full-system rehearsal | Passed all six checkpoints below on fresh source and restore named volumes after uncached rebuild of both services. |

The first development run exposed an error in the new rehearsal's SQLite connection setup; the harness was corrected and the entire rehearsal rerun from a fresh volume. The final run and container cleanup exited successfully. Production application code was unchanged.

## Independent deployment evidence

- Fresh named volume, exactly two services, no web database mount, unauthorized requests rejected.
- 48 exact bands and six currencies; clean 10000-row preview writes zero employees; atomic confirm and identical retry receipt.
- Independent workbook oracle: directory pages, full and filtered CSV, every summary/group, compa bucket and all 10000 leave-one-out equity results; 9500/250/250 positions.
- All table counts and content digests, bands, employee cents, preview rows, receipt and HR session survive down/up and recreation.
- Online SQLite backup includes live committed WAL marker; separate restored volume matches every table digest, all exact amounts, CSV, summaries, compa, equity and receipt.
- API outage maps to proxy 503 and recovers with preserved session/data and safe confirm retry.

The sample remained unchanged: SHA-256 `fab4866c8a2f3de000e42d88b8e25eccce717ae6c08e70eacea77901ca752573`. Independent workbook parsing confirmed country counts India 4,000; US 2,500; UK 1,200; Germany 1,000; Singapore 800; UAE 500. All 48 exact bands and six currencies matched. Preview had 10,000 valid, zero invalid and zero Unbanded rows and wrote zero employees. Confirmation inserted exactly 10,000 in source order, with 9,500 inside-band, 250 below and 250 above. Same-preview retries returned the identical durable receipt.

Directory first/second/last pages were checked for all three sizes, with total 10,000 and only the requested rows returned. Full CSV fields and exact amounts matched independently parsed workbook records in ID order. A combined search/country/department/level query and salary-descending CSV also matched independently selected and sorted records across the complete filtered population. Every country/department/level summary statistic, every compa bucket and percentage, and all 10,000 identity-excluded peer medians and equity percentages matched independent Fraction/integer calculations.

All table counts and sorted-content digests matched over down/up and force recreation, including employees, bands, identities, sessions, sealed preview rows/errors/context and completion receipt. Online backup captured a committed marker demonstrably absent from the main file and present only in WAL. A separate target volume restored it and matched all saved table digests before its new login. Restored CSV bytes, all summaries, compa-ratio, all equity results and receipt matched source responses. API outage produced 503 through the web and recovered to readiness with the same session, 10,000 records and identical confirm receipt. Protected directory, CSV, bands, analytics, preview and upload routes rejected unauthenticated access.

## Observed timings

Each entry is one complete HTTP response through the same-origin web proxy, measured with a monotonic clock and truncated to whole milliseconds; request/response body transfer and decoding are included. These are post-import observations over the complete 10,000-row dataset, with server-side pagination for directory requests. They are not percentile benchmarks or an approved SLA. Browser batch times above include test interaction and waits and should not be compared directly to these API timings.

| Flow | Milliseconds |
| --- | ---: |
| Workbook inspection, 10,000 rows | 700 |
| Full clean preview, 10,000 rows | 3082 |
| Atomic confirm and receipt, 10,000 rows | 1638 |
| Directory, 25 rows, page 1 | 10 |
| Directory, 25 rows, page 2 | 8 |
| Directory, 25 rows, page 400 | 13 |
| Directory, 50 rows, page 1 | 7 |
| Directory, 50 rows, page 2 | 12 |
| Directory, 50 rows, page 200 | 12 |
| Directory, 100 rows, page 1 | 8 |
| Directory, 100 rows, page 2 | 10 |
| Directory, 100 rows, page 100 | 16 |
| Full CSV, 10,000 rows | 102 |
| Directory, combined search and three filters | 16 |
| All salary summary groupings, 10,000 targets | 74 |
| Compa-ratio, 10,000 targets | 26 |
| Equity, all 10,000 targets and full saved peers | 393 |

## Reproduce and inspect

Follow [the runbooks](RUNBOOKS.md#rehearse-and-run-regressions). The independent runner accepts `--report /tmp/salary-acceptance.json`; CI runs it and preserves the timing report as an artifact. The current checked-in trace and this report record local results; they do not claim a future GitHub Actions run has passed.

Final test containers were stopped and removed. These disposable volumes were retained for inspection:

- `salary-acceptance-c2763497-source_salary_data`
- `salary-acceptance-43434e6f-restore_salary_data`
- `salary-smoke-0b0c5bb7_salary_data`
- `salary-browser-be9f108e_salary_data`

Local raw logs from this verification: `/tmp/incubyte-step12-host-backend.log`, `/tmp/incubyte-step12-backend-final.log`, `/tmp/incubyte-step12-browser.log`, `/tmp/incubyte-step12-smoke.log`, `/tmp/incubyte-step12-acceptance-final.log`. The structured timing report is `/tmp/incubyte-step12-report.json`. These temporary files are not required to interpret the committed acceptance trace and recorded results. Earlier disposable development volumes are not the final rehearsal evidence. No operational volume or local `.env` was accessed.
