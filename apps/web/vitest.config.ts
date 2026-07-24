import { defineConfig } from "vitest/config";

// Playwright owns e2e/*.spec.ts; vitest owns src unit tests only.
export default defineConfig({
  test: { include: ["src/**/*.test.ts"] },
});
