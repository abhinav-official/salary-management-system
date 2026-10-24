import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests", fullyParallel: false, workers: 1, retries: 0,
  timeout: 30_000, expect: { timeout: 10_000 }, reporter: "list",
  use: { ...devices["Desktop Chrome"], baseURL: process.env.E2E_ORIGIN,
    trace: "retain-on-failure", screenshot: "only-on-failure" },
});
