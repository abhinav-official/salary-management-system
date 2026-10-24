import { test, expect, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";

const row = (page: Page, id: string) => page.getByRole("row").filter({ has: page.getByRole("cell", { name: id, exact: true }) });
const search = (page: Page, value: string) => page.getByRole("searchbox", { name: "Search employees" }).fill(value);
const dialog = (page: Page) => page.getByRole("dialog");
const rows = (page: Page) => page.locator("tbody tr");

async function fillForm(page: Page, id: string) {
  const form = dialog(page);
  await form.getByLabel("Employee ID", { exact: true }).fill(id);
  await form.getByLabel("Name", { exact: true }).fill('Zoë, "नमस्ते"');
  await form.getByLabel("Email", { exact: true }).fill(`${id.toLowerCase()}@example.com`);
  await form.getByLabel("Department", { exact: true }).fill("People Operations");
  await form.getByLabel("Country", { exact: true }).selectOption("India");
  await form.getByLabel("Level", { exact: true }).selectOption("L1");
  await form.getByLabel("Annual salary", { exact: true }).fill("123.455");
}
async function currentAndHeaders(page: Page, id: string) {
  const current = await (await page.request.get(`/api/employees/records/${id}`)).json();
  const session = await (await page.request.get("/api/auth/session")).json();
  return { current, headers: { "Origin": process.env.E2E_ORIGIN!, "X-CSRF-Token": session.csrf_token, "If-Match": current.etag } };
}
function mutable(employee: Record<string, unknown>) {
  return Object.fromEntries(["name", "email", "department", "country", "level", "salary_amount"].map((field) => [field, employee[field]]));
}

test.beforeEach(async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Email", { exact: true }).fill(process.env.E2E_EMAIL!);
  await page.getByLabel("Password", { exact: true }).fill(process.env.E2E_PASSWORD!);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Employee directory", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Add employee", exact: true })).toBeEnabled();
  await expect(page.getByText("Loading your directory…")).not.toBeVisible();
});

test("creates, displays API exact values, edits and deletes with confirmation", async ({ page }) => {
  await page.getByRole("button", { name: "Add employee", exact: true }).click();
  await fillForm(page, "UI-CRUD");
  await dialog(page).getByRole("button", { name: "Create employee", exact: true }).click();
  await expect(dialog(page)).not.toBeVisible();
  await search(page, "UI-CRUD");
  await expect(row(page, "UI-CRUD")).toContainText("123.46");
  await expect(row(page, "UI-CRUD")).toContainText("Band-matched");
  await row(page, "UI-CRUD").getByRole("button", { name: "Edit UI-CRUD", exact: true }).click();
  await expect(dialog(page).getByLabel("Employee ID", { exact: true })).toHaveAttribute("readonly", "");
  await dialog(page).getByLabel("Name", { exact: true }).fill("Changed Name");
  await dialog(page).getByLabel("Annual salary", { exact: true }).fill("0.01");
  await dialog(page).getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(row(page, "UI-CRUD")).toContainText("Changed Name");
  await expect(row(page, "UI-CRUD")).toContainText("0.01");
  await row(page, "UI-CRUD").getByRole("button", { name: "Delete UI-CRUD", exact: true }).click();
  await dialog(page).getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(row(page, "UI-CRUD")).toBeVisible();
  await row(page, "UI-CRUD").getByRole("button", { name: "Delete UI-CRUD", exact: true }).click();
  await dialog(page).getByRole("button", { name: "Delete employee", exact: true }).click();
  await expect(page.getByRole("heading", { name: "No employees found", exact: true })).toBeVisible();
});

test("shows duplicate ID/email and API field validation without losing inputs", async ({ page }) => {
  await page.getByRole("button", { name: "Add employee", exact: true }).click();
  await fillForm(page, "emp00000");
  await dialog(page).getByLabel("Email", { exact: true }).fill("PERSON00000@EXAMPLE.COM");
  await dialog(page).getByRole("button", { name: "Create employee", exact: true }).click();
  await expect(dialog(page).getByLabel("Employee ID", { exact: true })).toHaveAttribute("aria-invalid", "true");
  await expect(dialog(page).getByLabel("Email", { exact: true })).toHaveAttribute("aria-invalid", "true");
  await expect(dialog(page).getByText("An employee with this employee_id already exists")).toBeVisible();
  await expect(dialog(page).getByLabel("Name", { exact: true })).toHaveValue('Zoë, "नमस्ते"');
  await dialog(page).getByLabel("Employee ID", { exact: true }).fill("UI-INVALID");
  await dialog(page).getByLabel("Email", { exact: true }).fill("ui-invalid@example.com");
  await dialog(page).getByLabel("Annual salary", { exact: true }).fill("-0.001");
  await dialog(page).getByRole("button", { name: "Create employee", exact: true }).click();
  await expect(dialog(page).getByLabel("Annual salary", { exact: true })).toHaveAttribute("aria-invalid", "true");
  await expect(dialog(page).getByLabel("Employee ID", { exact: true })).toHaveValue("UI-INVALID");
  await dialog(page).getByRole("button", { name: "Cancel", exact: true }).click();
});

test("handles an edit conflict and requires explicit reload before a new save", async ({ page }) => {
  await row(page, "EMP00000").getByRole("button", { name: "Edit EMP00000", exact: true }).click();
  await dialog(page).getByLabel("Name", { exact: true }).fill("Unsaved local edit");
  const { current, headers } = await currentAndHeaders(page, "EMP00000");
  expect((await page.request.put("/api/employees/records/EMP00000", { headers, data: { ...mutable(current), name: "Concurrent edit" } })).ok()).toBeTruthy();
  await dialog(page).getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(dialog(page).getByRole("alert")).toContainText("employee changed");
  await expect(dialog(page).getByLabel("Name", { exact: true })).toHaveValue("Unsaved local edit");
  await expect(dialog(page).getByRole("button", { name: "Save changes", exact: true })).toBeDisabled();
  await dialog(page).getByRole("button", { name: "Reload current employee", exact: true }).click();
  await expect(dialog(page).getByLabel("Name", { exact: true })).toHaveValue("Concurrent edit");
  await dialog(page).getByLabel("Name", { exact: true }).fill("Reviewed edit");
  await dialog(page).getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(row(page, "EMP00000")).toContainText("Reviewed edit");
});

test("rejects stale delete, reloads, and asks again before deletion", async ({ page }) => {
  await row(page, "EMP00001").getByRole("button", { name: "Delete EMP00001", exact: true }).click();
  const { current, headers } = await currentAndHeaders(page, "EMP00001");
  expect((await page.request.put("/api/employees/records/EMP00001", { headers, data: { ...mutable(current), name: "Another edit" } })).ok()).toBeTruthy();
  await dialog(page).getByRole("button", { name: "Delete employee", exact: true }).click();
  await expect(dialog(page).getByRole("alert")).toContainText("employee changed");
  await expect(dialog(page).getByRole("button", { name: "Delete employee", exact: true })).toBeDisabled();
  await dialog(page).getByRole("button", { name: "Reload current employee", exact: true }).click();
  await expect(dialog(page)).toContainText("Another edit");
  await dialog(page).getByRole("button", { name: "Cancel", exact: true }).click();
  expect((await page.request.get("/api/employees/records/EMP00001")).ok()).toBeTruthy();
});

test("country change asks for salary confirmation and uses the API currency/status", async ({ page }) => {
  await row(page, "EMP00002").getByRole("button", { name: "Edit EMP00002", exact: true }).click();
  const mutations: string[] = [];
  page.on("request", (request) => { if (request.method() === "PUT") mutations.push(request.url()); });
  await dialog(page).getByLabel("Country", { exact: true }).selectOption("US");
  await dialog(page).getByLabel("Annual salary", { exact: true }).fill("900.005");
  await expect(dialog(page).getByText(/Changing country changes/)).toBeVisible();
  await dialog(page).getByRole("button", { name: "Save changes", exact: true }).click();
  expect(mutations).toHaveLength(0);
  await dialog(page).getByRole("checkbox", { name: "I confirm the salary amount for US." }).check();
  await dialog(page).getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(row(page, "EMP00002")).toContainText("USD");
  await expect(row(page, "EMP00002")).toContainText("900.01");
  await expect(row(page, "EMP00002")).toContainText("Unbanded");
  expect(mutations).toHaveLength(1);
  expect(mutations[0]).toContain("country_change_confirmed=true");
});

test("requests 25/50/100 rows only; search, filters, sorts and size changes reset page", async ({ page }) => {
  const requests: string[] = [];
  const responses: { size: number; length: number }[] = [];
  page.on("request", (request) => { if (new URL(request.url()).pathname === "/api/employees") requests.push(request.url()); });
  page.on("response", async (response) => {
    if (new URL(response.url()).pathname === "/api/employees" && response.ok()) {
      const body = await response.json(); responses.push({ size: body.page_size, length: body.items.length });
    }
  });
  await expect(rows(page)).toHaveCount(25);
  await expect(page.getByText("10000 matching employees", { exact: true })).toBeVisible();
  for (const size of [50, 100, 25]) {
    await page.getByLabel("Rows per page", { exact: true }).selectOption(String(size));
    await expect(rows(page)).toHaveCount(size);
    await expect(page.getByText(`1–${size} of 10000`, { exact: true })).toBeVisible();
  }
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await expect(page.getByText("26–50 of 10000", { exact: true })).toBeVisible();
  await search(page, "person");
  await expect(page.getByText("1–25 of 10000", { exact: true })).toBeVisible();
  for (const [field, value] of [["Country", "India"], ["Level", "L1"], ["Department", "engineering"]]) {
    await page.getByRole("button", { name: "Next", exact: true }).click();
    await expect(page.getByRole("navigation", { name: "Directory pages" })).toContainText("Page 2");
    await page.getByLabel(field, { exact: true }).selectOption(value);
    await expect(page.getByRole("navigation", { name: "Directory pages" })).toContainText("Page 1");
  }
  await page.getByRole("button", { name: "Clear filters", exact: true }).click();
  await expect(page.getByRole("searchbox")).toHaveValue("");
  await expect(page.getByLabel("Country", { exact: true })).toHaveValue("");
  await expect(page.getByText("1–25 of 10000", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await page.getByRole("button", { name: "Sort by Annual salary", exact: true }).click();
  await expect(page.getByRole("navigation", { name: "Directory pages" })).toContainText("Page 1");
  await expect(page.getByText(/Order: Country ascending, salary asc within country/)).toBeVisible();
  await page.getByRole("button", { name: "Sort by Annual salary", exact: true }).click();
  await expect(page.getByText(/Order: Country ascending, salary desc within country/)).toBeVisible();
  const last = requests.at(-1)!;
  const apiPage = await (await page.request.get(last)).json();
  await expect(page.locator("tbody tr td:first-child")).toHaveText(apiPage.items.map((item: { employee_id: string }) => item.employee_id));
  expect(requests.length).toBeGreaterThan(8);
  expect(requests.every((url) => ["25", "50", "100"].includes(new URL(url).searchParams.get("page_size")!))).toBeTruthy();
  expect(responses.every((response) => response.length <= response.size)).toBeTruthy();
});

test("empty results, loading, directory failure, filter failure and retries", async ({ page }) => {
  await search(page, "NO-SUCH-EMPLOYEE");
  await expect(page.getByRole("heading", { name: "No employees found", exact: true })).toBeVisible();
  await expect(page.getByText("0–0 of 0", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Clear filters", exact: true }).click();
  await expect(rows(page)).toHaveCount(25);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/employees?**", async (route) => { await gate; await route.continue(); });
  await search(page, "Employee");
  await expect(page.getByText("Loading your directory…")).toBeVisible();
  await expect(page.getByRole("button", { name: "Download CSV", exact: true })).toBeDisabled();
  release();
  await expect(rows(page)).toHaveCount(25);
  await page.unroute("**/api/employees?**");
  await page.route("**/api/employees?**", (route) => route.fulfill({ status: 503, json: { detail: "Test API outage" } }));
  await search(page, "person");
  await expect(page.getByRole("heading", { name: "Unable to load employees", exact: true })).toBeVisible();
  await page.unroute("**/api/employees?**");
  await page.getByRole("button", { name: "Retry directory", exact: true }).click();
  await expect(rows(page)).toHaveCount(25);
  await page.route("**/api/employees/filter-options", (route) => route.fulfill({ status: 503, json: { detail: "Test filter outage" } }));
  await page.reload();
  await expect(page.getByText(/Unable to load filter options/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Add employee", exact: true })).toBeDisabled();
  await page.unroute("**/api/employees/filter-options");
  await page.getByRole("button", { name: "Retry filter options", exact: true }).click();
  await expect(page.getByRole("button", { name: "Add employee", exact: true })).toBeEnabled();
});

test("ignores a superseded search response", async ({ page }) => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/employees?**", async (route) => {
    if (new URL(route.request().url()).searchParams.get("search") === "delayed-search") {
      await gate;
      await route.fulfill({ status: 200, json: { items: [], total: 0, total_pages: 0, page: 1, page_size: 25, ordering: "employee_id asc" } }).catch(() => {});
    } else await route.continue();
  });
  await search(page, "delayed-search");
  await expect(page.getByText("Loading your directory…")).toBeVisible();
  await search(page, "person00005");
  await expect(rows(page)).toHaveCount(1);
  await expect(row(page, "EMP00005")).toBeVisible();
  release();
  await expect(row(page, "EMP00005")).toBeVisible();
  await page.unroute("**/api/employees?**");
});

test("CSV download matches full filtered API bytes and retries failures, including empty export", async ({ page }) => {
  await page.getByLabel("Country", { exact: true }).selectOption("India");
  await page.getByLabel("Level", { exact: true }).selectOption("L1");
  await page.getByLabel("Department", { exact: true }).selectOption("engineering");
  await search(page, "person");
  await page.getByRole("button", { name: "Sort by Name", exact: true }).click();
  await expect(rows(page)).toHaveCount(25);
  const expected = await page.request.get("/api/employees/export.csv?search=person&country=India&level=L1&department=engineering&sort=name&direction=asc");
  await page.route("**/api/employees/export.csv?**", (route) => route.fulfill({ status: 503, json: { detail: "CSV unavailable" } }));
  await page.getByRole("button", { name: "Download CSV", exact: true }).click();
  await expect(page.getByRole("region", { name: "Employee directory", exact: true }).getByRole("alert")).toContainText("CSV unavailable");
  await page.unroute("**/api/employees/export.csv?**");
  const downloaded = page.waitForEvent("download");
  await page.getByRole("button", { name: "Retry CSV download", exact: true }).click();
  const download = await downloaded;
  expect(download.suggestedFilename()).toBe("employees.csv");
  expect(await readFile((await download.path())!)).toEqual(await expected.body());
  await search(page, "NO-MATCH");
  await expect(page.getByRole("heading", { name: "No employees found", exact: true })).toBeVisible();
  const emptyDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download CSV", exact: true }).click();
  expect((await readFile((await (await emptyDownload).path())!)).toString()).toBe("Employee ID,Name,Email,Department,Level,Country,Currency,Annual Salary,Band Status\r\n");
});

test("deleting the only row on page two returns to page one", async ({ page }) => {
  await page.getByLabel("Department", { exact: true }).selectOption("last page");
  await expect(page.getByText("1–25 of 26", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await expect(page.getByText("26–26 of 26", { exact: true })).toBeVisible();
  await expect(rows(page)).toHaveCount(1);
  const id = await page.locator("tbody tr td:first-child").textContent();
  await page.getByRole("button", { name: `Delete ${id}`, exact: true }).click();
  await dialog(page).getByRole("button", { name: "Delete employee", exact: true }).click();
  await expect(page.getByText("1–25 of 25", { exact: true })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Directory pages" })).toContainText("Page 1 of 1");
  await expect(rows(page)).toHaveCount(25);
});

test("API presentation values are rendered without salary or band calculation", async ({ page }) => {
  const real = await (await page.request.get("/api/employees?page_size=25")).json();
  real.items[0].salary_amount = "123.45";
  real.items[0].salary_minor = "999999999999999999";
  real.items[0].currency = "API currency";
  real.items[0].band_status = "API band status";
  await page.route("**/api/employees?**", (route) => route.fulfill({ json: real }));
  await search(page, "presentation-check");
  await expect(row(page, "EMP00000")).toContainText("API currency 123.45");
  await expect(row(page, "EMP00000")).toContainText("API band status");
});

test("creates a record for restart verification and works on a narrow screen", async ({ page }) => {
  await page.getByRole("button", { name: "Add employee", exact: true }).click();
  await fillForm(page, "UI-PERSIST");
  await dialog(page).getByRole("button", { name: "Create employee", exact: true }).click();
  await expect(dialog(page)).not.toBeVisible();
  await search(page, "UI-PERSIST");
  await expect(row(page, "UI-PERSIST")).toContainText("123.46");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("button", { name: "Add employee", exact: true })).toBeVisible();
  await expect(page.getByRole("searchbox")).toBeVisible();
  await page.screenshot({ path: "test-results/directory-mobile.png", fullPage: true });
});

test("record persists after restart", async ({ page }) => {
  await search(page, "UI-PERSIST");
  await expect(row(page, "UI-PERSIST")).toContainText('Zoë, "नमस्ते"');
  await expect(row(page, "UI-PERSIST")).toContainText("INR 123.46");
  await expect(row(page, "UI-PERSIST")).toContainText("Band-matched");
  await page.screenshot({ path: "test-results/directory-after-restart.png", fullPage: true });
});
