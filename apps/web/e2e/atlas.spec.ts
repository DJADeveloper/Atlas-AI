/**
 * M09 acceptance flows (docs/60), serial against one backend:
 *
 * 1. Abstention renders its distinct state (empty index — must run
 *    before anything is seeded).
 * 2. Settings profile toggle round-trips and shows in /health.
 * 3. Adding a source via the UI triggers ingestion; jobs reflect
 *    progress within 5 s.
 * 4. Send a message → first streamed token ≤ 3 s → citation chip →
 *    source viewer opens with the cited chunk highlighted.
 * 5. Dark and light themes both render the chat screen (visual smoke;
 *    pixel-diff baselines join when a human blesses them).
 */

import { expect, test } from "@playwright/test";
import path from "node:path";

const API = "http://localhost:8000";
const FIXTURES = path.resolve(__dirname, "fixtures/notes");

test.describe.serial("Atlas UI", () => {
  test("abstention renders a distinct not-found state", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("chat-input").fill("What is the quarterly synergy cadence?");
    await page.getByTestId("chat-send").click();
    await expect(page.getByTestId("abstention-message")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId("abstention-message")).toContainText(
      "Not found in your documents",
    );
  });

  test("profile toggle round-trips through settings and /health", async ({ page, request }) => {
    await page.goto("/settings");
    await page.getByTestId("profile-local-only").check();
    await expect(page.getByTestId("settings-saved")).toBeVisible();
    const health = await request.get(`${API}/health`);
    expect((await health.json()).profile).toBe("local-only");
    await page.getByTestId("profile-hybrid").check();
    await expect(page.getByTestId("settings-saved")).toBeVisible();
    const reverted = await request.get(`${API}/health`);
    expect((await reverted.json()).profile).toBe("hybrid");
  });

  test("adding a source triggers ingestion and jobs show progress", async ({ page }) => {
    await page.goto("/sources");
    await page.getByTestId("source-name").fill("Notes");
    await page.getByTestId("source-uri").fill(FIXTURES);
    await page.getByTestId("source-add").click();
    await expect(page.getByTestId("source-list")).toContainText("Notes");

    await page.goto("/jobs");
    // Progress must be visible within 5 s of backend state change.
    await expect(page.getByTestId("jobs-list").locator("tr").first()).toBeVisible({
      timeout: 5_000,
    });
    // The echo/hash pipeline is fast; the job reaches a terminal state.
    await expect(page.getByTestId("jobs-list")).toContainText("succeeded", { timeout: 30_000 });
  });

  test("a question streams, cites, and the chip opens the highlighted chunk", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("new-conversation").click();
    await page.getByTestId("chat-input").fill("What is the Meridian notice period?");

    const sendAt = Date.now();
    await page.getByTestId("chat-send").click();
    // First streamed token visible within 3 s (echo provider).
    await expect(page.getByTestId("assistant-message")).toContainText("Based on", {
      timeout: 3_000,
    });
    expect(Date.now() - sendAt).toBeLessThanOrEqual(3_000);

    const chip = page.getByTestId("citation-chip-1");
    await expect(chip).toBeVisible({ timeout: 15_000 });
    await chip.click();

    await expect(page.getByTestId("source-viewer")).toBeVisible();
    const highlighted = page.getByTestId("highlighted-chunk");
    await expect(highlighted).toBeVisible();
    await expect(highlighted).toContainText("notice period");
  });

  test("dark and light themes both render the chat screen", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("chat-screen")).toBeVisible();
    const light = await page.screenshot({ fullPage: true });
    expect(light.byteLength).toBeGreaterThan(0);

    await page.getByTestId("theme-toggle").click();
    await expect(page.locator("html")).toHaveClass(/dark/);
    const dark = await page.screenshot({ fullPage: true });
    expect(dark.byteLength).toBeGreaterThan(0);
    expect(Buffer.compare(light, dark)).not.toBe(0); // themes visibly differ
  });
});
