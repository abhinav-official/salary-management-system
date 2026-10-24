# Prompts and decisions used with AI tools

I directed this build through the prompts summarized below. These are condensed first-person paraphrases of my prompts and accepted decisions in this session, not a verbatim transcript. Where testing found a problem, I distinguish the resulting fix from my original instruction. I have omitted routine command output and progress updates.

## Rules I set before the requirements were written

I asked for a salary management system for an HR manager, starting with `docs/REQUIREMENTS.md` before any implementation or technology choices. I required a `.gitignore`, Git initialized on `main`, and a requirements commit.

I set the compa-ratio rule in that opening prompt: compare salary with the matching band midpoint using the unrounded ratio, in exactly three buckets—below 80%, 80 to 120%, and above 120%. I later made the inclusive 80% and 120% boundaries explicit and required exact integer cross-multiplication. A ratio of 79.999% must remain below 80% even if its display rounds to 80.00%.

I also set the Unbanded rule before the requirements were written: an employee without a matching country-and-level band can still be saved and imported. They count in headcount and ordinary salary statistics. Only compa-ratio and equity exclude them, and those views must show the excluded count. Missing bands and out-of-band salaries must not become validation failures.

I required 10,000 employees; directory CRUD; ID, name and email search; department, level and country filters; sorting; and server-side pagination with 25 rows by default and options for 50 and 100. I specified six countries—India, US, UK, Germany, Singapore and UAE—and eight levels, L1–L8, with one minimum/midpoint/maximum band per country and level in its own currency.

I required exact money stored and calculated as integer minor units, never floating-point salary values, with half-up rounding to two decimals. I required a two-phase Excel import: validate every row and report original Excel row numbers in preview, then permit confirmation only for a clean preview. Any invalid row must fail the whole import; existing records must never be silently overwritten or skipped.

I asked for average, median and pay spread, keeping even-count medians as the mean of the two middle values. Equity must compare an employee with peers in the same country and level, excluding themselves. CSV export must use the directory's search and filters. I approved the resulting [requirements](REQUIREMENTS.md) before moving on.

## Reproducible data and a deployment-first plan

I asked for `tools/generate_dataset.py`, fixed seed 42, and `data/employees_10000.xlsx` with Employees, Salary Bands and About sheets. I specified unique IDs and emails, country counts of 4,000/2,500/1,200/1,000/800/500 respectively, all 48 bands, and exactly 9,500 salaries inside bands, 250 below and 250 above. I required pinned generator dependencies, regeneration instructions, two byte-identical runs and independent verification of the saved workbook's counts before committing.

After accepting the dataset, I requested [the build plan](PLAN.md). I selected FastAPI to own all salary logic, Next.js with TypeScript to call the API without database access, and SQLite in WAL mode. Docker Compose must have exactly `api` and `web`, with SQLite on a named volume so data survives restarts. I explicitly required deployment scaffolding first, rather than leaving deployment until the end.

I required small implementation steps, each tested and committed before the next. I reiterated exact integer/Decimal money, atomic import confirmation against the current database, full-population analytics rather than page-based calculations, and identity-excluded peer medians. I then asked for a new public GitHub repository with a clear salary-management name and a push of `main`; the project was published as [salary-management-system](https://github.com/abhinav-official/salary-management-system).

## Foundations, directory and exact analytics

I first asked for both service skeletons, API health through the same-origin web proxy, the single-HR sign-in foundation, migrations and environment examples. Before committing, I required a clean Compose build/start, readiness through web, WAL verification, a sentinel surviving recreation, and proof that web has no database mount.

**Step 1 proxy correction:** the sign-in checks found that an expected API authentication error became a proxy 503. Node Fetch was attempting to replay a streamed POST body after a 401. The implemented fix forwarded cookies explicitly and set Fetch credentials to `omit`, disabling that automatic credential handling. The reproduction then returned the expected 403, 401 and 200 responses, and the deployment/persistence checks passed. I accepted step 1 and proceeded.

I next required shared exact-money parsing and formatting, rejection of negatives before rounding, and the `123.455 → 12346` minor-unit case. I asked for ordered migrations, normalized case-insensitive uniqueness, revisions, ordered band amounts with a positive midpoint, and no employee-to-band foreign key so Unbanded employees remain valid. Migration preservation had to be tested on real temporary SQLite files.

For bands, I required an API-owned command to validate and atomically load all 48 workbook bands only into an empty catalog. An existing catalog must produce an explicit conflict; startup must never reseed or recreate deleted bands. Band CRUD needed revision checks, and deleting a band must preserve employees and change only their derived eligibility.

For employees, I required immutable IDs, revision-checked edits, structured field/conflict errors and case-insensitive duplicate ID/email rejection. I asked for one shared directory query with trimmed case-insensitive search, combined filters, allowlisted sorting, L1–L8 order, salary sorting grouped by country, and employee ID as the stable tie-breaker. CSV must export every matching row in that order, neutralize spreadsheet formulas, and retain exact two-decimal amounts with a separate currency column.

I asked for the directory screen on those APIs, including field errors, delete confirmation, CSV download, count/range display and 25/50/100 paging. Search/filter changes must reset page 1; deleting the last row on a page must step back. Changing country during editing must ask for confirmation because it changes salary currency context. The frontend must render API salary/status values, perform no salary or band calculations, and never fetch the whole directory. I required Compose browser checks, loading/empty/failure recovery, conflicts, export parity and restart persistence.

For summaries, I required headcount, average, median, minimum, maximum and spread over the full filtered population, grouped by country and by department/level within country. Currencies must never blend. Empty statistics must be null, singleton spread zero, and 100.00/100.01 must retain the exact average/median 100.005 until displaying 100.01. Compa-ratio must reconcile total, eligible and excluded counts and explicitly handle zero eligible employees.

For equity, I clarified that filters select targets, while peers come from the full saved Band-matched country/level population. Exclusion must be by identity, removing exactly one occurrence when salaries tie. One other eligible peer is enough; no peers and zero peer median need separate states. The API must compute the percentage from the unrounded peer median, reconcile all four states, and fetch/sort each peer group once without per-employee queries. I required comparisons with an independent reference, including the 100/200/400 example and all 10,000 sample employees.

## Atomic import and the band/import screens

I separated preview and confirmation into steps 8 and 9. For preview, I required workbook inspection, worksheet choice when ambiguous, corrupt/formula/header rejection, raw decimal cell parsing without float conversion, and validation of every nonblank row. Every normalized duplicate participant must be marked, not just the later occurrence. Preview must expose all field errors, existing-record conflicts, original Excel row numbers, band status and valid/invalid/Unbanded totals, including errors beyond the first page.

I required an immutable persisted preview with owner, worksheet, source digest, normalized integer amounts, band status/revisions, creation time and a 30-minute expiry. Its opaque ID must provide paged rows/errors and explicit clean/invalid status. Preview must write zero employees; I explicitly deferred confirmation to the next step.

For confirmation, I required one `BEGIN IMMEDIATE` transaction and revalidation of employee revisions, the full band catalog, duplicates/conflicts and every row's derived status against current data. Any difference must fail the entire operation with a specific report and zero inserts. Success must insert in preview order and save a durable receipt in the same transaction. Retrying the same completed preview must return that receipt without another insert, including after a lost response or restart. I required rollback injection, separate-connection concurrency tests, busy-database retry handling, stale/invalid/expired/foreign rejection and valid Unbanded imports.

For step 10, I asked for band forms and impact-aware deletion, and workbook upload, explicit multi-sheet choice, complete preview/error paging, clean-only confirmation, expiry/conflict recovery and receipt display. The frontend must send salary inputs unchanged and display API values and errors. I required full-size upload/download/proxy tests and safe retries.

**Step 10 harness corrections:** browser tests could not reliably read streamed POST response bodies from Chromium's inspector cache. The harness instead read persisted preview metadata through the authenticated API using the displayed preview ID. Alert assertions were scoped to the application to avoid Next.js's route announcer; the preview page-size selector was corrected, and error-page checks were synchronized with responses. The clean rerun passed 23 browser checks, including the 10,000-row import, CSV parity and restart/receipt recovery. I explicitly said the screenshot path was fine, asked that it remain unchanged, and directed the step's commit and push.

## Analytics interface, focus and final acceptance

For step 11, I asked for summary and equity screens with independent filters, currency labels, all compa buckets and Unbanded counts, explicit equity unavailable states, and a note that peers span the full country/level group. Every displayed number and percentage must come from the API without frontend rounding or aggregation. I required content-shaped skeleton loaders, failure alerts with retry, success toasts, and refresh of directory, summary and equity after mutations. Browser checks had to include core keyboard interactions and error focus.

**Step 11 focus correction:** testing showed that React's removal of a dialog left keyboard focus behind. The fix explicitly restored focus to the opener; API error alerts also received focus. A supposed singleton fixture shared a peer group with an employee moved by an earlier directory test, so the fixture was moved to an unused group rather than changing correct peer logic. The native-select keyboard sequence was corrected as well. The final 32-browser-check run passed, along with 174 backend tests and frontend gates; I accepted the step and requested final acceptance.

For step 12, I required [runbooks](RUNBOOKS.md), a trace of every requirements section 12 scenario, and fixes only where checks demonstrated a problem. I required rebuilt images, fresh disposable volumes, explicit band loading, full sample preview/confirm, and independent exact calculations for directory, CSV, summaries, compa-ratio and equity. I also required down/up and recreation persistence, a WAL-aware backup restored into a separate volume, exact restored counts/amounts, outage recovery, unauthorized rejection, all regressions and recorded timings without inventing a latency SLA.

**Step 12 evidence:** two precise acceptance tests were added for Excel errors at rows 7/12 and a failure after 5,000 inserts in a 10,000-row confirmation. The latter left zero employees, identities or receipt writes before a successful safe retry. The new rehearsal's connection-setup error was corrected and the entire rehearsal rerun from a fresh volume; no application behavior changes were needed. The final evidence mapped all 20 scenarios, passed 176 backend tests and 32 browser tests, and verified every restored table digest plus a committed marker present only in WAL. All 10,000 independent equity comparisons and salary summaries matched. The [recorded results](ACCEPTANCE_RESULTS.md) include observed directory timings of 7–16 ms, summaries 74 ms, compa-ratio 26 ms, equity 393 ms, preview 3,082 ms and confirm 1,638 ms. Step 12 was committed and pushed as `c24f714`.

Finally, I asked for this committed prompts artifact, limited to what actually happened in this session, in my voice as the person directing the build, with the commit message `docs: record prompts used with AI tools` and a push to GitHub.
