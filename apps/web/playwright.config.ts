import { defineConfig } from "@playwright/test";

/**
 * E2E against the real stack (M09): Postgres + Redis + the API and
 * worker running with the deterministic echo/hash providers — no
 * Ollama, no API key. CI starts the backend; this config owns only
 * the web server. Flows run in declaration order on one worker:
 * abstention must run before any source is indexed.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  retries: process.env.CI ? 1 : 0,
  workers: 1, // ordered: the flows build on one backend's state
  reporter: process.env.CI ? [["github"], ["list"]] : [["list"]],
  use: {
    baseURL: "http://localhost:3000",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  webServer: {
    command: "pnpm start",
    port: 3000,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
});
