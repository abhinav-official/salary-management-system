import { test, expect, type Page } from "@playwright/test";
import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";

type Receipt = { preview_id: string; actor: string; completed_at: number; inserted_count: number; employee_ids: string[] };
const fixtures = process.env.E2E_IMPORT_FIXTURES!;
const nav = (page: Page, name: string) => page.getByRole("navigation", { name: "Workspace", exact: true }).getByRole("button", { name, exact: true }).click();
const dialog = (page: Page) => page.getByRole("dialog");
const bandRow = (page: Page, country: string, level: string) => page.getByRole("row").filter({ has: page.getByRole("cell", { name: country, exact: true }) }).filter({ has: page.getByRole("cell", { name: level, exact: true }) });
const total = (page: Page, label: string) => page.locator("dt").filter({ hasText: new RegExp(`^${label}$`) }).locator("..").locator("dd");
const previewPath = (id: string) => `/api/imports/previews/${id}`;
async function auth(page: Page) {
  const session = await (await page.request.get("/api/auth/session")).json();
  return { Origin: process.env.E2E_ORIGIN!, "X-CSRF-Token": session.csrf_token };
}
async function count(page: Page) { return (await (await page.request.get("/api/employees")).json()).total; }
async function upload(page: Page, name: string, multiple = false) {
  await nav(page, "Excel import");
  await page.getByLabel("Workbook (.xlsx)", { exact: true }).setInputFiles(name === "sample" ? path.resolve("../data/employees_10000.xlsx") : path.join(fixtures, name));
  const inspected = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/imports/inspect");
  await page.getByRole("button", { name: "Inspect workbook", exact: true }).click();
  expect((await inspected).status()).toBe(200);
  if (multiple) {
    await expect(page.getByLabel("Worksheet", { exact: true })).toHaveValue("");
    await expect(page.getByRole("button", { name: "Create a new preview", exact: true })).toBeDisabled();
    await page.getByLabel("Worksheet", { exact: true }).selectOption("Employees");
  }
}
async function preview(page: Page) {
  const created = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/imports/previews" && response.request().method() === "POST");
  await page.getByRole("button", { name: "Create a new preview", exact: true }).click();
  const response = await created;
  expect(response.status()).toBe(201);
  await expect(page.getByRole("region", { name: "Import review", exact: true })).toBeVisible();
  const id = await page.getByLabel("Saved preview ID", { exact: true }).inputValue();
  const result = await (await page.request.get(previewPath(id))).json();
  await expect(page.getByRole("heading", { name: `Preview: ${result.status}`, exact: true })).toBeVisible();
  await expect(page.getByText("Loading preview rows…", { exact: true })).not.toBeVisible();
  return result;
}
async function bandForm(page: Page, country = "Germany", level = "L8") {
  await page.getByRole("button", { name: "Add salary band", exact: true }).click();
  await dialog(page).getByLabel("Country", { exact: true }).selectOption(country);
  await dialog(page).getByLabel("Level", { exact: true }).selectOption(level);
  await dialog(page).getByLabel("Minimum", { exact: true }).fill("123.455");
  await dialog(page).getByLabel("Midpoint", { exact: true }).fill("200");
  await dialog(page).getByLabel("Maximum", { exact: true }).fill("300");
}
async function savedBand(page: Page) {
  await dialog(page).getByRole("button", { name: "Create band", exact: true }).click();
  await expect(dialog(page)).not.toBeVisible();
}

test.beforeEach(async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Email", { exact: true }).fill(process.env.E2E_EMAIL!);
  await page.getByLabel("Password", { exact: true }).fill(process.env.E2E_PASSWORD!);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Employee directory", exact: true })).toBeVisible();
});

test("band forms render exact API values and field errors, conflict reload, delete impact and derived status after recreation", async ({ page }) => {
  await nav(page, "Salary bands");
  await expect(bandRow(page, "India", "L1")).toBeVisible();
  await bandForm(page);
  await dialog(page).getByLabel("Minimum", { exact: true }).fill("-0.001");
  await dialog(page).getByRole("button", { name: "Create band", exact: true }).click();
  await expect(dialog(page).getByLabel("Minimum", { exact: true })).toHaveAttribute("aria-invalid", "true");
  await expect(dialog(page)).toContainText("Money cannot be negative");
  await dialog(page).getByLabel("Minimum", { exact: true }).fill("999");
  await dialog(page).getByRole("button", { name: "Create band", exact: true }).click();
  await expect(dialog(page).getByRole("alert")).toContainText("minimum <= midpoint <= maximum");
  await dialog(page).getByLabel("Minimum", { exact: true }).fill("123.455");
  await savedBand(page);
  await expect(bandRow(page, "Germany", "L8")).toContainText("123.46");
  await page.screenshot({ path: "test-results/salary-bands.png", fullPage: true });
  await bandForm(page);
  await dialog(page).getByRole("button", { name: "Create band", exact: true }).click();
  await expect(dialog(page).getByRole("alert")).toContainText("already exists");
  await dialog(page).getByRole("button", { name: "Cancel", exact: true }).click();
  await bandRow(page, "Germany", "L8").getByRole("button", { name: "Edit Germany L8", exact: true }).click();
  const current = await (await page.request.get("/api/bands/Germany/L8")).json();
  const h = await auth(page);
  expect((await page.request.put("/api/bands/Germany/L8", { headers: { ...h, "If-Match": current.etag }, data: { minimum_amount: current.minimum_amount, midpoint_amount: "250.00", maximum_amount: current.maximum_amount } })).ok()).toBeTruthy();
  await dialog(page).getByLabel("Midpoint", { exact: true }).fill("225.00");
  await dialog(page).getByRole("button", { name: "Save band", exact: true }).click();
  await expect(dialog(page).getByRole("button", { name: "Save band", exact: true })).toBeDisabled();
  await expect(dialog(page).getByLabel("Midpoint", { exact: true })).toHaveValue("225.00");
  await dialog(page).getByRole("button", { name: "Reload current band", exact: true }).click();
  await expect(dialog(page).getByLabel("Midpoint", { exact: true })).toHaveValue("250.00");
  await dialog(page).getByRole("button", { name: "Save band", exact: true }).click();
  await expect(dialog(page)).not.toBeVisible();
  const impact = await (await page.request.get("/api/bands/Germany/L8")).json();
  const employeeBefore = await (await page.request.get("/api/employees/records/EMP00015")).json();
  await bandRow(page, "Germany", "L8").getByRole("button", { name: "Delete Germany L8", exact: true }).click();
  await expect(dialog(page)).toContainText(`Affected employees: ${impact.affected_employee_count}.`);
  await expect(dialog(page)).toContainText("affected employees will become Unbanded");
  await dialog(page).getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(bandRow(page, "Germany", "L8")).toBeVisible();
  await bandRow(page, "Germany", "L8").getByRole("button", { name: "Delete Germany L8", exact: true }).click();
  await dialog(page).getByRole("button", { name: "Delete band", exact: true }).click();
  await expect(dialog(page)).not.toBeVisible();
  await expect(bandRow(page, "Germany", "L8")).toHaveCount(0);
  await nav(page, "Employee directory");
  await page.getByRole("searchbox", { name: "Search employees" }).fill("EMP00015");
  await expect(page.getByRole("cell", { name: "Unbanded", exact: true })).toBeVisible();
  const after = await (await page.request.get("/api/employees/records/EMP00015")).json();
  expect(after.salary_minor).toBe(employeeBefore.salary_minor);
  expect(after.revision).toBe(employeeBefore.revision);
  await nav(page, "Salary bands");
  await expect(page.getByRole("button", { name: "Add salary band", exact: true })).toBeEnabled();
  await bandForm(page); await savedBand(page);
  await nav(page, "Employee directory");
  await expect(page.getByRole("searchbox", { name: "Search employees" })).toHaveValue("EMP00015");
  await page.getByRole("searchbox", { name: "Search employees" }).fill("EMP00015");
  await expect(page.getByRole("cell", { name: "Band-matched", exact: true })).toBeVisible();
  await nav(page, "Salary bands");
  await bandRow(page, "Germany", "L8").getByRole("button", { name: "Delete Germany L8", exact: true }).click();
  await expect(dialog(page).getByRole("button", { name: "Delete band", exact: true })).toBeEnabled();
  const changed = await (await page.request.get("/api/bands/Germany/L8")).json();
  expect((await page.request.put("/api/bands/Germany/L8", { headers: { ...h, "If-Match": changed.etag }, data: {
    minimum_amount: changed.minimum_amount, midpoint_amount: "250.00", maximum_amount: changed.maximum_amount,
  } })).ok()).toBeTruthy();
  await dialog(page).getByRole("button", { name: "Delete band", exact: true }).click();
  await expect(dialog(page).getByRole("button", { name: "Delete band", exact: true })).toBeDisabled();
  await dialog(page).getByRole("button", { name: "Reload current band", exact: true }).click();
  await expect(dialog(page).getByRole("button", { name: "Delete band", exact: true })).toBeEnabled();
  await dialog(page).getByRole("button", { name: "Cancel", exact: true }).click();
});

test("invalid preview includes every error page, source row numbers, duplicates, formulas and backend bypass rejection", async ({ page }) => {
  await upload(page, "invalid.xlsx");
  const before = await count(page);
  const result = await preview(page);
  expect(result.status).toBe("invalid");
  await expect(total(page, "Invalid rows")).toHaveText("120");
  await expect(page.getByRole("button", { name: "Confirm import", exact: true })).toBeDisabled();
  const bypass = await page.request.post(previewPath(result.preview_id) + "/confirm", { headers: await auth(page), data: {} });
  expect(bypass.status()).toBe(409);
  expect((await bypass.json()).detail.code).toBe("preview_invalid");
  await page.getByRole("combobox", { name: "Preview rows per page", exact: true }).selectOption("100");
  await expect(page.getByRole("region", { name: "Import preview rows" }).locator("tbody tr")).toHaveCount(100);
  await expect(page.getByRole("region", { name: "Import preview rows" })).toContainText("Formulas are not allowed");
  await expect(page.getByText(/Every occurrence of this duplicate/).first()).toBeVisible();
  await page.getByRole("button", { name: "Next preview page", exact: true }).click();
  const apiRows = await (await page.request.get(previewPath(result.preview_id) + "/rows?page=2&page_size=100")).json();
  await expect(page.getByRole("region", { name: "Import preview rows" }).locator("tbody tr td:first-child")).toHaveText(apiRows.items.map((row: { row_number: number }) => String(row.row_number)));
  await expect(page.getByText("Excel row 102 · Annual Salary", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "All errors", exact: true }).click();
  await page.getByRole("combobox", { name: "Preview rows per page", exact: true }).selectOption("25");
  let seen = 0;
  let nextPage = 2;
  const collected: string[] = [];
  do {
    await expect(page.getByText("Loading preview errors…", { exact: true })).not.toBeVisible();
    const errors = page.locator(".preview-pages > .issue-list > li");
    seen += await errors.count();
    collected.push(...await errors.allTextContents());
    const next = page.getByRole("button", { name: "Next preview page", exact: true });
    if (await next.isDisabled()) break;
    await Promise.all([
      page.waitForResponse((response) => {
        const url = new URL(response.url());
        return url.pathname.endsWith("/errors") && url.searchParams.get("page") === String(nextPage) && url.searchParams.get("page_size") === "25";
      }),
      next.click(),
    ]);
    await expect(page.getByRole("navigation", { name: "Preview pages", exact: true })).toContainText(`Page ${nextPage} of`);
    nextPage++;
  } while (seen < 1000);
  expect(seen).toBe(result.error_count);
  expect(collected.join(" ")).toContain("Excel row 121");
  expect(collected.join(" ")).toContain("nothing will be overwritten or skipped");
  expect(await count(page)).toBe(before);
  await page.screenshot({ path: "test-results/import-invalid.png", fullPage: true });
});

test("corrupt and missing duplicate headers show API errors and retain selected files", async ({ page }) => {
  await nav(page, "Excel import");
  await page.getByLabel("Workbook (.xlsx)", { exact: true }).setInputFiles(path.join(fixtures,"corrupt.xlsx"));
  await page.getByRole("button", { name: "Inspect workbook", exact: true }).click();
  await expect(page.locator("main").getByRole("alert")).toContainText("Workbook is corrupt");
  await expect(page.getByText("Selected file: corrupt.xlsx", { exact: true })).toBeVisible();
  await upload(page, "headers.xlsx");
  const info = await preview(page);
  expect(info.status).toBe("invalid");
  await page.getByRole("button", { name: "All errors", exact: true }).click();
  await expect(page.locator(".preview-pages")).toContainText("Excel row 4");
  await expect(page.locator(".preview-pages")).toContainText("Missing required header: Annual Salary");
  await expect(page.locator(".preview-pages")).toContainText("Header appears more than once");
  await expect(page.getByRole("button", { name: "Confirm import", exact: true })).toBeDisabled();
});

test("valid multi-sheet preview keeps selection across navigation and lost response retries return one receipt", async ({ page }) => {
  await upload(page, "valid.xlsx", true);
  const before = await count(page);
  const info = await preview(page);
  await expect(total(page, "Valid rows")).toHaveText("2");
  await expect(total(page, "Invalid rows")).toHaveText("0");
  await expect(total(page, "Unbanded rows")).toHaveText("1");
  await expect(page.getByRole("region", { name: "Import preview rows" })).toContainText("123.46");
  await expect(page.getByRole("region", { name: "Import preview rows" })).toContainText("Unbanded");
  await expect(page.getByText("Expires:", { exact: false })).toBeVisible();
  await page.screenshot({ path: "test-results/import-preview.png", fullPage: true });
  await nav(page, "Salary bands"); await nav(page, "Excel import");
  await expect(page.getByLabel("Worksheet", { exact: true })).toHaveValue("Employees");
  await expect(page.getByText("Selected file: valid.xlsx", { exact: true })).toBeVisible();
  let saved: Receipt | undefined;
  await page.route("**" + previewPath(info.preview_id) + "/confirm", async (route) => {
    const response = await route.fetch(); expect(response.status()).toBe(200); saved = await response.json(); await route.abort("failed");
  });
  await page.getByRole("button", { name: "Confirm import", exact: true }).click();
  await expect(page.getByRole("button", { name: "Retry same confirmation safely", exact: true })).toBeEnabled();
  expect(await count(page)).toBe(before + 2);
  await expect(page.getByLabel("Workbook (.xlsx)", { exact: true })).toBeDisabled();
  await page.unroute("**" + previewPath(info.preview_id) + "/confirm");
  const response = page.waitForResponse((response) => response.url().endsWith("/confirm"));
  await page.getByRole("button", { name: "Retry same confirmation safely", exact: true }).click();
  expect((await response).status()).toBe(200);
  expect(await (await page.request.get(previewPath(info.preview_id)+"/receipt")).json()).toEqual(saved);
  const receipt = page.getByRole("region", { name: "Completion receipt", exact: true });
  await expect(receipt).toContainText("Inserted employees: 2");
  await receipt.getByText("Ordered employee IDs", { exact: true }).click();
  await expect(receipt.locator("li")).toHaveText(saved!.employee_ids);
  await page.reload();
  await nav(page, "Excel import");
  await expect(page.getByRole("region", { name: "Completion receipt", exact: true })).toContainText(info.preview_id);
  expect(await count(page)).toBe(before + 2);
});

test("stale preview blocks confirmation, retains workbook and sheet, and permits a fresh preview", async ({ page }) => {
  await upload(page, "stale.xlsx");
  const before = await count(page);
  const old = await preview(page);
  const band = await (await page.request.get("/api/bands/India/L1")).json();
  expect((await page.request.put("/api/bands/India/L1", { headers: { ...await auth(page), "If-Match": band.etag }, data: { minimum_amount: band.minimum_amount, midpoint_amount: "201.00", maximum_amount: band.maximum_amount } })).ok()).toBeTruthy();
  await page.getByRole("button", { name: "Confirm import", exact: true }).click();
  await expect(page.locator("main").getByRole("alert")).toContainText("Database or validated values changed");
  await expect(page.getByRole("button", { name: "Confirm import", exact: true })).toBeDisabled();
  await page.getByText("API error details", { exact: true }).click();
  await expect(page.locator("main").getByRole("alert")).toContainText("band_changes");
  expect(await count(page)).toBe(before);
  await expect(page.getByText("Selected file: stale.xlsx", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Worksheet", { exact: true })).toHaveValue("Employees");
  const fresh = await preview(page);
  expect(fresh.preview_id).not.toBe(old.preview_id);
  await expect(page.getByRole("button", { name: "Confirm import", exact: true })).toBeEnabled();
});

test("expired owned preview is rejected by API and recovery screen", async ({ page }) => {
  const id = process.env.E2E_EXPIRED_PREVIEW_ID!;
  const before = await count(page);
  const bypass = await page.request.post(previewPath(id)+"/confirm",{ headers: await auth(page), data:{} });
  expect(bypass.status()).toBe(410);
  expect((await bypass.json()).detail.code).toBe("preview_expired");
  await nav(page,"Excel import");
  await page.getByLabel("Saved preview ID",{exact:true}).fill(id);
  await page.getByRole("button",{name:"Resume saved preview",exact:true}).click();
  await expect(page.locator("main").getByRole("alert")).toContainText("Preview expired");
  await expect(page.getByRole("button",{name:"Confirm import",exact:true})).not.toBeVisible();
  expect(await count(page)).toBe(before);
});

test("API presentation, page failures and a retryable confirmation conflict are rendered without salary logic", async ({ page }) => {
  await upload(page,"busy.xlsx");
  const info = await preview(page);
  const real = await (await page.request.get(previewPath(info.preview_id)+"/rows")).json();
  real.items[0].salary_amount = "API amount"; real.items[0].currency = "API currency"; real.items[0].band_status = "API band status";
  await page.route("**"+previewPath(info.preview_id)+"/rows?**",route=>route.fulfill({json:real}));
  await page.getByRole("combobox", { name: "Preview rows per page", exact:true}).selectOption("50");
  await expect(page.getByRole("region",{name:"Import preview rows"})).toContainText("API currency API amount");
  await expect(page.getByRole("region",{name:"Import preview rows"})).toContainText("API band status");
  await page.unroute("**"+previewPath(info.preview_id)+"/rows?**");
  await page.route("**"+previewPath(info.preview_id)+"/rows?**",route=>route.fulfill({status:503,json:{detail:"Preview page unavailable"}}));
  await page.getByRole("combobox", { name: "Preview rows per page", exact:true}).selectOption("100");
  await expect(page.locator("main").getByRole("alert").filter({hasText:"Preview page unavailable"}).last()).toBeVisible();
  await page.unroute("**"+previewPath(info.preview_id)+"/rows?**");
  await page.getByRole("button",{name:"Retry preview page",exact:true}).click();
  await expect(page.getByRole("region",{name:"Import preview rows"})).toContainText("123.46");
  await page.route("**"+previewPath(info.preview_id)+"/confirm",route=>route.fulfill({status:409,json:{detail:{code:"database_busy",message:"Database is busy; retry this same preview",retryable:true}}}));
  await page.getByRole("button",{name:"Confirm import",exact:true}).click();
  await expect(page.locator("main").getByRole("alert").first()).toContainText("Database is busy");
  await page.unroute("**"+previewPath(info.preview_id)+"/confirm");
  await page.getByRole("button",{name:"Retry same confirmation safely",exact:true}).click();
  await expect(page.getByRole("region",{name:"Completion receipt",exact:true})).toContainText("Inserted employees: 2");
});

test("band list loading failure and API display values have retry paths", async ({ page }) => {
  await page.route("**/api/bands",route=>route.fulfill({status:503,json:{detail:"Band catalog unavailable"}}));
  await nav(page,"Salary bands");
  await expect(page.locator("main").getByRole("alert")).toContainText("Band catalog unavailable");
  await page.unroute("**/api/bands");
  const catalog = await (await page.request.get("/api/bands")).json();
  catalog.items[0].minimum_amount="API minimum"; catalog.items[0].currency="API currency";
  await page.route("**/api/bands",route=>route.fulfill({json:catalog}));
  await page.getByRole("button",{name:"Retry salary bands",exact:true}).click();
  await expect(page.getByRole("cell",{name:"API minimum",exact:true})).toBeVisible();
  await expect(page.getByRole("cell",{name:"API currency",exact:true})).toBeVisible();
});

test("full sample upload preview confirmation and CSV download retain API parity for all 10000 rows", async ({ page }) => {
  test.setTimeout(180_000);
  const before = await count(page);
  await nav(page,"Salary summaries");
  await expect(page.getByText(`Selected employees: ${before}`,{exact:true})).toBeVisible();
  await nav(page,"Pay equity");
  await expect(page.locator("dt").filter({hasText:/^Selected targets$/}).locator("..").locator("dd")).toHaveText(String(before));
  await upload(page,"sample",true);
  const info = await preview(page);
  expect(info.total_rows).toBe(10000); expect(info.valid_count).toBe(10000); expect(info.invalid_count).toBe(0); expect(info.unbanded_count).toBe(0);
  await expect(total(page,"Total rows")).toHaveText("10000");
  await expect(total(page,"Valid rows")).toHaveText("10000");
  await page.getByRole("combobox", { name: "Preview rows per page", exact:true}).selectOption("100");
  await expect(page.getByRole("region",{name:"Import preview rows"}).locator("tbody tr")).toHaveCount(100);
  const first = await (await page.request.get(previewPath(info.preview_id)+"/rows?page_size=100")).json();
  await expect(page.getByRole("region",{name:"Import preview rows"}).locator("tbody tr td:nth-child(2)")).toHaveText(first.items.map((item:{values:{employee_id:string}})=>item.values.employee_id));
  expect(await count(page)).toBe(before);
  const confirmed = page.waitForResponse(response=>response.url().endsWith("/confirm"));
  await page.getByRole("button",{name:"Confirm import",exact:true}).press("Enter");
  const response = await confirmed; expect(response.status()).toBe(200);
  const receipt:Receipt = await (await page.request.get(previewPath(info.preview_id)+"/receipt")).json(); expect(receipt.inserted_count).toBe(10000); expect(new Set(receipt.employee_ids).size).toBe(10000);
  await expect(page.getByRole("region",{name:"Completion receipt",exact:true})).toContainText("Inserted employees: 10000");
  expect(await count(page)).toBe(before+10000);
  expect(await (await page.request.post(previewPath(info.preview_id)+"/confirm",{headers:await auth(page),data:{}})).json()).toEqual(receipt);
  await writeFile(process.env.E2E_IMPORT_RECEIPT_FILE!,JSON.stringify(receipt),{mode:0o600});
  await nav(page,"Salary summaries");
  await expect(page.getByText(`Selected employees: ${before+10000}`,{exact:true})).toBeVisible();
  await nav(page,"Pay equity");
  await expect(page.locator("dt").filter({hasText:/^Selected targets$/}).locator("..").locator("dd")).toHaveText(String(before+10000));
  await nav(page,"Employee directory");
  await expect(page.getByRole("button",{name:"Download CSV",exact:true})).toBeEnabled();
  const expected = await page.request.get("/api/employees/export.csv");
  const downloaded = page.waitForEvent("download");
  await page.getByRole("button",{name:"Download CSV",exact:true}).click();
  const download=await downloaded;
  expect(await readFile((await download.path())!)).toEqual(await expected.body());
  await page.screenshot({path:"test-results/full-import-directory.png",fullPage:true});
});

test("import receipt and saved employees persist after restart", async ({ page }) => {
  const receipt:Receipt=JSON.parse(await readFile(process.env.E2E_IMPORT_RECEIPT_FILE!,"utf8"));
  await nav(page,"Excel import");
  await page.getByLabel("Saved preview ID",{exact:true}).fill(receipt.preview_id);
  await page.getByRole("button",{name:"Resume saved preview",exact:true}).click();
  await expect(page.getByRole("region",{name:"Completion receipt",exact:true})).toContainText("Inserted employees: 10000");
  const retry=await page.request.post(previewPath(receipt.preview_id)+"/confirm",{headers:await auth(page),data:{}});
  expect(retry.status()).toBe(200); expect(await retry.json()).toEqual(receipt);
  expect((await page.request.get(`/api/employees/records/${receipt.employee_ids[0]}`)).ok()).toBeTruthy();
  expect((await page.request.get(`/api/employees/records/${receipt.employee_ids.at(-1)}`)).ok()).toBeTruthy();
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:"test-results/import-receipt-mobile.png",fullPage:true});
});
