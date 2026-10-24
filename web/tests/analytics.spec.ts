import { test, expect, type Page } from "@playwright/test";

const nav = (page: Page, name: string) => page.getByRole("navigation", { name: "Workspace", exact: true }).getByRole("button", { name, exact: true }).click();
const summary = (page: Page) => page.getByRole("region", { name: "Salary summaries", exact: true });
const equity = (page: Page) => page.getByRole("region", { name: "Pay equity", exact: true });
const dialog = (page: Page) => page.getByRole("dialog");
const metric = (page: Page, name: string) => page.locator("dt").filter({ hasText: new RegExp(`^${name}$`) }).locator("..").locator("dd");
async function filters(page: Page, view: "summary" | "equity", values: { country?: string; department?: string; level?: string }) {
  const panel = view === "summary" ? summary(page) : equity(page);
  for (const [field, value] of Object.entries(values)) await panel.getByLabel(field === "country" ? "Country" : field === "department" ? "Department" : "Level", { exact: true }).selectOption(value!);
}
async function ready(page: Page, view: "summary" | "equity") {
  await expect(page.getByRole("status", { name: view === "summary" ? "Loading salary summaries" : "Loading equity comparisons", exact: true })).not.toBeVisible();
}

test.beforeEach(async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Email", { exact: true }).fill(process.env.E2E_EMAIL!);
  await page.getByLabel("Password", { exact: true }).fill(process.env.E2E_PASSWORD!);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Employee directory", exact: true })).toBeVisible();
});

test("summary filters are independent, full population counts and every grouping keep currencies separate", async ({ page }) => {
  const directory = await (await page.request.get("/api/employees?page_size=25")).json();
  expect(directory.items).toHaveLength(25); expect(directory.total).toBeGreaterThan(10000);
  await page.getByRole("searchbox", { name: "Search employees" }).fill("NO-MATCH-ANALYTICS");
  await expect(page.getByRole("heading", { name: "No employees found", exact: true })).toBeVisible();
  const requests: string[] = [];
  page.on("request", (request) => { if (request.url().includes("/api/analytics/")) requests.push(request.url()); });
  await nav(page, "Salary summaries");
  await expect(summary(page).getByText(`Selected employees: ${directory.total}`, { exact: true })).toBeVisible();
  const all = await (await page.request.get("/api/analytics/summaries")).json();
  const rows = summary(page).getByRole("region", { name: "Salary summary results" }).locator("tbody tr");
  await expect(rows).toHaveCount(6);
  await expect(rows.locator("td:nth-child(1)")).toHaveText(all.countries.map((group: { country: string }) => group.country));
  await expect(rows.locator("td:nth-child(2)")).toHaveText(all.countries.map((group: { currency: string }) => group.currency));
  await expect(rows.locator("td:nth-child(3)")).toHaveText(all.countries.map((group: { count: number }) => String(group.count)));
  await filters(page, "summary", { department: "analytics states" });
  const expected = await (await page.request.get("/api/analytics/summaries?department=analytics+states")).json();
  await expect(summary(page).getByText(`Selected employees: ${expected.total_count}`, { exact: true })).toBeVisible();
  for (const grouping of ["departments", "levels", "countries"]) {
    await summary(page).getByLabel("Group by", { exact: true }).selectOption(grouping);
    await expect(rows).toHaveCount(expected[grouping].length);
    const currencyColumn = grouping === "countries" ? 2 : 3;
    await expect(rows.locator(`td:nth-child(${currencyColumn})`)).toHaveText(expected[grouping].map((group: { currency: string }) => group.currency));
  }
  const compa = await (await page.request.get("/api/analytics/compa-ratio?department=analytics+states")).json();
  await expect(metric(page, "Total employees")).toHaveText(String(compa.total_count));
  await expect(metric(page, "Eligible employees")).toHaveText(String(compa.eligible_count));
  await expect(metric(page, "Excluded Unbanded employees")).toHaveText(String(compa.excluded_unbanded_count));
  expect(compa.eligible_count + compa.excluded_unbanded_count).toBe(compa.total_count);
  for (const bucket of compa.buckets) {
    const card = summary(page).getByRole("article", { name: bucket.label, exact: true });
    await expect(card.locator(".metric-value")).toHaveText(String(bucket.count));
    await expect(card).toContainText(`${bucket.percentage}%`);
  }
  expect(requests.length).toBeGreaterThan(2);
  expect(requests.every((url) => [...new URL(url).searchParams.keys()].every((key) => ["country", "department", "level"].includes(key)))).toBeTruthy();
  await page.screenshot({ path: "test-results/salary-summaries.png", fullPage: true });
});

test("exact even median and average stay 100.01, no eligible and empty summaries show API states", async ({ page }) => {
  await nav(page, "Salary summaries");
  await filters(page, "summary", { country: "US", department: "analytics even", level: "L7" });
  await expect(summary(page).getByText("Selected employees: 2", { exact: true })).toBeVisible();
  const row = summary(page).getByRole("region", { name: "Salary summary results" }).locator("tbody tr");
  await expect(row).toContainText("USD");
  await expect(row.locator("td:nth-child(4)")).toHaveText("100.01");
  await expect(row.locator("td:nth-child(5)")).toHaveText("100.01");
  await expect(row.locator("td:nth-child(8)")).toHaveText("0.01");
  await summary(page).getByRole("button", { name: "Clear summary filters", exact: true }).click();
  await filters(page, "summary", { country: "UK", department: "analytics states", level: "L2" });
  await expect(metric(page, "Eligible employees")).toHaveText("0");
  await expect(metric(page, "Excluded Unbanded employees")).toHaveText("1");
  await expect(summary(page).getByText("No eligible employees for compa-ratio.", { exact: true })).toBeVisible();
  await expect(summary(page).getByRole("article")).toHaveCount(3);
  await expect(summary(page).getByText("Percentage unavailable", { exact: true })).toHaveCount(3);
  await filters(page, "summary", { country: "UAE", department: "analytics even", level: "L8" });
  await expect(summary(page).getByText("No employees match these summary filters.", { exact: true })).toBeVisible();
  await expect(row).toContainText("AED");
  await expect(row).toContainText("No data");
  await expect(row.locator("td:nth-child(4)")).toHaveText("—");
});

test("equity states reconcile and filtered targets retain full country and level peers", async ({ page }) => {
  await nav(page, "Pay equity");
  await filters(page, "equity", { department: "analytics states" });
  await expect(metric(page, "Selected targets")).toHaveText("4");
  for (const label of ["Available comparisons", "Excluded Unbanded targets", "Insufficient peers", "Zero peer median"]) await expect(metric(page, label)).toHaveText("1");
  const table = equity(page).getByRole("region", { name: "Equity comparison results" });
  await expect(table.getByRole("cell", { name: "Insufficient peers", exact: true })).toBeVisible();
  await expect(table.getByRole("cell", { name: "Peer median is zero", exact: true })).toBeVisible();
  await expect(table.getByRole("cell", { name: "Unbanded", exact: true })).toHaveCount(2);
  await expect(table.getByRole("row").filter({ has: page.getByRole("cell", { name: "AN-ZERO1", exact: true }) }).locator("td:nth-child(10)")).toHaveText("0.00");
  await filters(page, "equity", { department: "analytics target", country: "US", level: "L1" });
  await expect(metric(page, "Selected targets")).toHaveText("1");
  const target = table.locator("tbody tr");
  await expect(target).toContainText("AN-TARGET");
  await expect(target.locator("td:nth-child(9)")).toHaveText("2");
  await expect(target.locator("td:nth-child(10)")).toHaveText("300.00");
  await expect(target.locator("td:nth-child(11)")).toHaveText("-66.67%");
  await expect(equity(page).getByText(/Peers span the full saved country and level group/)).toBeVisible();
  await nav(page, "Salary summaries");
  await expect(summary(page).getByLabel("Country", { exact: true })).toHaveValue("");
  await nav(page, "Pay equity");
  await expect(equity(page).getByLabel("Department", { exact: true })).toHaveValue("analytics target");
  await filters(page, "equity", { country: "UAE", level: "L8" });
  await expect(equity(page).getByRole("heading", { name: "No equity targets", exact: true })).toBeVisible();
});

test("equity detail pages leave full-population counts and API percentages unchanged", async ({ page }) => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/analytics/equity*", async (route) => { await gate; await route.continue(); });
  await nav(page, "Pay equity");
  const loading = page.getByRole("status", { name: "Loading equity comparisons", exact: true });
  await expect(loading.locator(".metric-card")).toHaveCount(5);
  await expect(loading.locator("thead th")).toHaveCount(12);
  release(); await ready(page, "equity"); await page.unroute("**/api/analytics/equity*");
  const response = await (await page.request.get("/api/analytics/equity")).json();
  await expect(metric(page, "Selected targets")).toHaveText(String(response.total_count));
  const rows = equity(page).getByRole("region", { name: "Equity comparison results" }).locator("tbody tr");
  for (const size of [25, 50, 100]) {
    await equity(page).getByLabel("Equity rows per page", { exact: true }).selectOption(String(size));
    await expect(rows).toHaveCount(size);
    await expect(metric(page, "Selected targets")).toHaveText(String(response.total_count));
  }
  await equity(page).getByRole("button", { name: "Next equity page", exact: true }).click();
  await expect(rows.locator("td:first-child")).toHaveText(response.items.slice(100, 200).map((item: { employee_id: string }) => item.employee_id));
  await expect(metric(page, "Excluded Unbanded targets")).toHaveText(String(response.excluded_unbanded_count));
  await page.setViewportSize({ width: 390, height: 844 });
  const table = equity(page).getByRole("region", { name: "Equity comparison results", exact: true });
  await table.focus(); await page.keyboard.press("ArrowRight");
  await expect.poll(() => table.evaluate((node) => node.scrollLeft)).toBeGreaterThan(0);
  await page.screenshot({ path: "test-results/equity-mobile.png", fullPage: true });
});

test("skeletons match cards and tables, failures focus alerts and keyboard retry succeeds", async ({ page }) => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/analytics/summaries*", async (route) => { await gate; await route.continue(); });
  await nav(page, "Salary summaries");
  const loading = page.getByRole("status", { name: "Loading salary summaries", exact: true });
  await expect(loading).toBeVisible(); await expect(loading).toHaveAttribute("aria-busy", "true");
  await expect(loading.locator(".metric-card")).toHaveCount(6);
  await expect(loading.locator("tbody tr")).toHaveCount(6);
  await page.screenshot({ path: "test-results/summary-skeleton.png", fullPage: true });
  release(); await ready(page, "summary"); await page.unroute("**/api/analytics/summaries*");
  await page.route("**/api/analytics/summaries*", (route) => route.fulfill({ status: 503, json: { detail: "Summary service unavailable" } }));
  await summary(page).getByRole("button", { name: "Refresh summaries", exact: true }).click();
  const alert = summary(page).getByRole("alert");
  await expect(alert).toContainText("Summary service unavailable"); await expect(alert).toBeFocused();
  await page.unroute("**/api/analytics/summaries*");
  await page.keyboard.press("Tab");
  await expect(summary(page).getByRole("button", { name: "Retry salary summaries", exact: true })).toBeFocused();
  await page.keyboard.press("Enter"); await ready(page, "summary");
  await expect(alert).not.toBeVisible();
  await page.route("**/api/analytics/equity*", (route) => route.fulfill({ status: 503, json: { detail: "Equity service unavailable" } }));
  await nav(page, "Pay equity");
  const equityAlert = equity(page).getByRole("alert");
  await expect(equityAlert).toBeFocused(); await expect(equityAlert).toContainText("Equity service unavailable");
  await page.unroute("**/api/analytics/equity*");
  await page.keyboard.press("Tab"); await page.keyboard.press("Enter"); await ready(page, "equity");
  await expect(equityAlert).not.toBeVisible();
});

test("API display strings are not rounded, recomputed, or used to reassign buckets", async ({ page }) => {
  const real = await (await page.request.get("/api/analytics/summaries?country=US&department=analytics+even&level=L7")).json();
  real.countries[0].average.amount = "100.01000"; real.countries[0].median.amount = "API median"; real.countries[0].median.minor_numerator = "99999999999999999999";
  const compa = await (await page.request.get("/api/analytics/compa-ratio?country=US&department=analytics+even&level=L7")).json();
  compa.total_count = 777; compa.eligible_count = 444; compa.excluded_unbanded_count = 333;
  compa.buckets[0] = { key: "below_80", label: "Below 80% (API classification)", count: 123, percentage: "80.0000" };
  await page.route("**/api/analytics/summaries*", (route) => route.fulfill({ json: real }));
  await page.route("**/api/analytics/compa-ratio*", (route) => route.fulfill({ json: compa }));
  await nav(page, "Salary summaries");
  await expect(summary(page).getByRole("cell", { name: "100.01000", exact: true })).toBeVisible();
  await expect(summary(page).getByRole("cell", { name: "API median", exact: true })).toBeVisible();
  await expect(metric(page, "Total employees")).toHaveText("777");
  await expect(metric(page, "Eligible employees")).toHaveText("444");
  await expect(metric(page, "Excluded Unbanded employees")).toHaveText("333");
  await expect(summary(page).getByRole("article", { name: "Below 80% (API classification)", exact: true })).toContainText("80.0000%");
  const comparisons = await (await page.request.get("/api/analytics/equity?department=analytics+target")).json();
  comparisons.items[0].percentage_difference = "-66.67000"; comparisons.items[0].peer_median.amount = "API peer median"; comparisons.items[0].peer_median.minor_numerator = "1";
  await page.route("**/api/analytics/equity*", (route) => route.fulfill({ json: comparisons }));
  await nav(page, "Pay equity");
  await expect(equity(page).getByRole("cell", { name: "-66.67000%", exact: true })).toBeVisible();
  await expect(equity(page).getByRole("cell", { name: "API peer median", exact: true })).toBeVisible();
});

test("employee and band mutations refresh preserved summary, equity and directory criteria and show success toasts", async ({ page }) => {
  await nav(page, "Salary summaries"); await filters(page, "summary", { department: "analytics even", country: "US", level: "L7" });
  await expect(summary(page).getByText("Selected employees: 2", { exact: true })).toBeVisible();
  await nav(page, "Pay equity"); await filters(page, "equity", { department: "analytics even", country: "US", level: "L7" });
  await expect(metric(page, "Selected targets")).toHaveText("2");
  await nav(page, "Employee directory");
  await page.getByRole("button", { name: "Add employee", exact: true }).click();
  for (const [label, value] of [["Employee ID", "AN-NEW"], ["Name", "New analytics employee"], ["Email", "an-new@example.com"], ["Department", "Analytics Even"], ["Annual salary", "300.00"]]) await dialog(page).getByLabel(label, { exact: true }).fill(value);
  await dialog(page).getByLabel("Country", { exact: true }).selectOption("US"); await dialog(page).getByLabel("Level", { exact: true }).selectOption("L7");
  await dialog(page).getByRole("button", { name: "Create employee", exact: true }).click();
  await expect(page.getByRole("status", { name: "Success notification", exact: true })).toContainText("AN-NEW created");
  await nav(page, "Salary summaries"); await expect(summary(page).getByText("Selected employees: 3", { exact: true })).toBeVisible();
  await nav(page, "Pay equity"); await expect(metric(page, "Selected targets")).toHaveText("3");
  await nav(page, "Employee directory"); await page.getByRole("searchbox", { name: "Search employees" }).fill("AN-NEW");
  await page.getByRole("button", { name: "Edit AN-NEW", exact: true }).click();
  await dialog(page).getByLabel("Annual salary", { exact: true }).fill("200.005");
  await dialog(page).getByRole("button", { name: "Save changes", exact: true }).click();
  await nav(page, "Salary summaries");
  const expected = await (await page.request.get("/api/analytics/summaries?country=US&department=analytics+even&level=L7")).json();
  await expect(summary(page).getByRole("cell", { name: expected.countries[0].average.amount, exact: true })).toBeVisible();
  await nav(page, "Employee directory");
  await expect(page.getByRole("searchbox", { name: "Search employees" })).toHaveValue("AN-NEW");
  await page.getByRole("button", { name: "Delete AN-NEW", exact: true }).click(); await dialog(page).getByRole("button", { name: "Delete employee", exact: true }).click();
  await nav(page, "Salary summaries"); await expect(summary(page).getByText("Selected employees: 2", { exact: true })).toBeVisible();
  await nav(page, "Salary bands"); await page.getByRole("button", { name: "Delete US L7", exact: true }).click();
  await expect(dialog(page)).toContainText("Affected employees: 2."); await dialog(page).getByRole("button", { name: "Delete band", exact: true }).click();
  await expect(page.getByRole("status", { name: "Success notification", exact: true })).toContainText("US L7 deleted");
  await nav(page, "Salary summaries"); await expect(metric(page, "Excluded Unbanded employees")).toHaveText("2"); await expect(metric(page, "Eligible employees")).toHaveText("0");
  await nav(page, "Pay equity"); await expect(metric(page, "Excluded Unbanded targets")).toHaveText("2");
  await nav(page, "Employee directory"); await page.getByRole("searchbox", { name: "Search employees" }).fill("AN-EVEN");
  await expect(page.getByRole("cell", { name: "Unbanded", exact: true })).toHaveCount(2);
  await nav(page, "Salary bands"); await page.getByRole("button", { name: "Add salary band", exact: true }).click();
  await dialog(page).getByLabel("Country", { exact: true }).selectOption("US"); await dialog(page).getByLabel("Level", { exact: true }).selectOption("L7");
  for (const [label, value] of [["Minimum", "0"], ["Midpoint", "100"], ["Maximum", "1000"]]) await dialog(page).getByLabel(label, { exact: true }).fill(value);
  await dialog(page).getByRole("button", { name: "Create band", exact: true }).click();
  await expect(dialog(page)).not.toBeVisible();
  await nav(page, "Salary summaries"); await expect(metric(page, "Eligible employees")).toHaveText("2"); await expect(metric(page, "Excluded Unbanded employees")).toHaveText("0");
  await nav(page, "Pay equity"); await expect(metric(page, "Available comparisons")).toHaveText("2");
  await nav(page, "Salary bands"); await page.getByRole("button", { name: "Edit US L7", exact: true }).click();
  await dialog(page).getByLabel("Midpoint", { exact: true }).fill("200.00"); await dialog(page).getByRole("button", { name: "Save band", exact: true }).click();
  await expect(dialog(page)).not.toBeVisible();
  await nav(page, "Salary summaries");
  await expect(summary(page).getByRole("article", { name: "Below 80%", exact: true }).locator(".metric-value")).toHaveText("2");
});

test("keyboard core form, API error focus, currency confirmation, table and delete confirmation", async ({ page }) => {
  const add = page.getByRole("button", { name: "Add employee", exact: true });
  await expect(add).toBeEnabled(); await add.focus(); await page.keyboard.press("Enter");
  await expect(dialog(page).getByRole("button", { name: "Close dialog", exact: true })).toBeFocused();
  await page.keyboard.press("Tab");
  for (const value of ["AN-KEYBOARD", "Keyboard user", "an-keyboard@example.com", "Analytics Keyboard"]) { await page.keyboard.type(value); await page.keyboard.press("Tab"); }
  await page.keyboard.press("Tab"); await page.keyboard.press("Tab");
  await expect(dialog(page).getByLabel("Annual salary", { exact: true })).toBeFocused();
  await page.keyboard.type("-0.001"); await page.keyboard.press("Enter");
  await expect(dialog(page).getByRole("alert")).toBeFocused();
  await expect(dialog(page).getByLabel("Annual salary", { exact: true })).toHaveAttribute("aria-invalid", "true");
  await page.keyboard.press("Shift+Tab"); await expect(dialog(page).getByLabel("Annual salary", { exact: true })).toBeFocused();
  await page.keyboard.press("ControlOrMeta+A"); await page.keyboard.type("123.455"); await page.keyboard.press("Enter");
  await expect(dialog(page)).not.toBeVisible(); await expect(add).toBeFocused();
  await page.getByRole("searchbox", { name: "Search employees" }).fill("AN-KEYBOARD");
  await page.setViewportSize({ width: 390, height: 844 });
  const table = page.getByRole("region", { name: "Employee results", exact: true });
  await table.focus(); await expect(table).toBeFocused(); await page.keyboard.press("ArrowRight");
  await expect.poll(() => table.evaluate((node) => node.scrollLeft)).toBeGreaterThan(0);
  await page.getByRole("button", { name: "Edit AN-KEYBOARD", exact: true }).focus(); await page.keyboard.press("Enter");
  await dialog(page).getByLabel("Country", { exact: true }).focus(); await page.keyboard.press("u"); await page.keyboard.press("Tab");
  await expect(dialog(page).getByLabel("Country", { exact: true })).not.toHaveValue("India");
  const country = await dialog(page).getByLabel("Country", { exact: true }).inputValue();
  const confirmation = dialog(page).getByRole("checkbox", { name: `I confirm the salary amount for ${country}.`, exact: true });
  await confirmation.focus(); await page.keyboard.press("Space"); await expect(confirmation).toBeChecked();
  await dialog(page).getByRole("button", { name: "Save changes", exact: true }).focus(); await page.keyboard.press("Enter");
  await expect(dialog(page)).not.toBeVisible();
  const remove = page.getByRole("button", { name: "Delete AN-KEYBOARD", exact: true });
  await remove.focus(); await page.keyboard.press("Enter"); await page.keyboard.press("Escape"); await expect(dialog(page)).not.toBeVisible(); await expect(remove).toBeFocused();
  expect((await page.request.get("/api/employees/records/AN-KEYBOARD")).ok()).toBeTruthy();
  await page.keyboard.press("Enter"); await dialog(page).getByRole("button", { name: "Delete employee", exact: true }).focus(); await page.keyboard.press("Enter");
  await expect(dialog(page)).not.toBeVisible();
  expect((await page.request.get("/api/employees/records/AN-KEYBOARD")).status()).toBe(404);
  await nav(page, "Salary bands");
  const addBand = page.getByRole("button", { name: "Add salary band", exact: true });
  await expect(addBand).toBeEnabled(); await addBand.focus(); await page.keyboard.press("Enter");
  await expect(dialog(page).getByRole("button", { name: "Close dialog", exact: true })).toBeFocused();
  await page.keyboard.press("Tab"); await page.keyboard.press("Tab"); await page.keyboard.press("Tab");
  await expect(dialog(page).getByLabel("Minimum", { exact: true })).toBeFocused();
  await page.keyboard.type("-0.001"); await page.keyboard.press("Tab"); await page.keyboard.type("100");
  await page.keyboard.press("Tab"); await page.keyboard.type("200"); await page.keyboard.press("Enter");
  await expect(dialog(page).getByRole("alert")).toBeFocused();
  await expect(dialog(page).getByLabel("Minimum", { exact: true })).toHaveAttribute("aria-invalid", "true");
  await page.keyboard.press("Escape"); await expect(dialog(page)).not.toBeVisible(); await expect(addBand).toBeFocused();
});


test("filter options retry and superseded analytics filter responses retain the latest criteria", async ({ page }) => {
  await page.route("**/api/employees/filter-options", (route) => route.fulfill({ status: 503, json: { detail: "Analytics filters unavailable" } }));
  await nav(page, "Salary summaries");
  await expect(summary(page).getByRole("alert")).toContainText("Analytics filters unavailable");
  await expect(summary(page).getByLabel("Country", { exact: true })).toBeDisabled();
  await page.unroute("**/api/employees/filter-options");
  await summary(page).getByRole("button", { name: "Retry summary filter options", exact: true }).click();
  await expect(summary(page).getByLabel("Country", { exact: true })).toBeEnabled();
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/analytics/summaries*", async (route) => {
    if (new URL(route.request().url()).searchParams.get("country") === "India") {
      await gate;
      await route.fulfill({ json: { total_count: 0, countries: [], departments: [], levels: [] } }).catch(() => {});
    } else await route.continue();
  });
  const delayed = page.waitForRequest((request) => request.url().includes("/analytics/summaries?") && new URL(request.url()).searchParams.get("country") === "India");
  await filters(page, "summary", { country: "India" }); await delayed;
  await filters(page, "summary", { country: "US" });
  const current = await (await page.request.get("/api/analytics/summaries?country=US")).json();
  await expect(summary(page).getByText(`Selected employees: ${current.total_count}`, { exact: true })).toBeVisible();
  release();
  await expect(summary(page).getByLabel("Country", { exact: true })).toHaveValue("US");
  await expect(summary(page).getByText(`Selected employees: ${current.total_count}`, { exact: true })).toBeVisible();
  await page.unroute("**/api/analytics/summaries*");
  await page.route("**/api/analytics/summaries*", async (route) => {
    if (new URL(route.request().url()).searchParams.get("country") === "India") await route.fulfill({ status: 503, json: { detail: "Temporary country failure" } });
    else await route.continue();
  });
  await filters(page, "summary", { country: "India" });
  await expect(summary(page).getByRole("alert")).toContainText("Temporary country failure");
  await filters(page, "summary", { country: "US" });
  await expect(summary(page).getByText(`Selected employees: ${current.total_count}`, { exact: true })).toBeVisible();
  await page.unroute("**/api/analytics/summaries*");
  await filters(page, "summary", { country: "India" });
  const restored = await (await page.request.get("/api/analytics/summaries?country=India")).json();
  await expect(summary(page).getByText(`Selected employees: ${restored.total_count}`, { exact: true })).toBeVisible();
  await expect(summary(page).getByRole("alert")).not.toBeVisible();
});
