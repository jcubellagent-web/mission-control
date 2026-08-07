import { expect, test } from "@chromatic-com/playwright";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { Page } from "@playwright/test";

const dashboardFixture = readFileSync(resolve(__dirname, "../tests/fixtures/dashboard-data.ci.json"), "utf8");
const hotFixture = readFileSync(resolve(__dirname, "../tests/fixtures/control-tower-hot.ci.json"), "utf8");
const fixtureEvents = JSON.stringify({ source: "Chromatic fixture", events: [] });

async function installDeterministicDashboardFixture(page: Page) {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.addInitScript(() => {
    const fixedNow = new Date("2026-01-01T12:00:00.000Z").valueOf();
    const NativeDate = Date;
    class FixedDate extends NativeDate {
      constructor(...args: ConstructorParameters<typeof Date>) {
        super(...(args.length ? args : [fixedNow]));
      }
      static now() {
        return fixedNow;
      }
    }
    Object.setPrototypeOf(FixedDate, NativeDate);
    window.Date = FixedDate as DateConstructor;
  });
  await page.route("**/data/*.json", (route) => route.fulfill({ status: 404 }));
  await page.route("**/api/**", (route) => route.fulfill({ status: 404 }));
  await page.route("**/events/mission-control", (route) => route.abort());
  await page.route("**/data/control-tower-live.json", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: dashboardFixture,
  }));
  await page.route("**/api/control-tower-hot", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: hotFixture,
  }));
  await page.route("**/api/live-events", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: fixtureEvents,
  }));
}

async function openControlTower(page: Page) {
  await installDeterministicDashboardFixture(page);
  await page.goto("/");

  await expect(page).toHaveTitle("Josh 2.0 | Control Tower");
  await expect(page.locator("#root")).not.toBeEmpty();
  await expect(page.locator("#today-jobs")).toBeVisible();
}

test("renders the approved desktop Control Tower", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await openControlTower(page);

  await expect(page.locator("main")).toBeVisible();
  await expect(page.locator("#today-jobs")).toBeVisible();
  await expect(page.locator(".agent-activity-evidence").first()).toBeVisible();
  await expect(page.locator(".agent-hero-card[data-work-motion]").first()).toHaveAttribute("data-work-motion", /live|paused/);
  await expect(page.locator("#brain-atlas [data-agent-route]").first()).toHaveAttribute("data-agent-route", /live|active-stale|idle/);
  const providers = page.locator("#finops-dashboard [data-finops-region='provider']");
  await expect(providers).toHaveCount(4);
  await expect(providers.first().locator(".finops-provider-utilization span")).toHaveText("Live heat");
  await expect(providers.first()).toHaveAttribute("data-quota-state", /verified|unavailable/);
  await expect(providers.filter({ hasText: "Ollama" }).locator(".finops-provider-quota")).toContainText("Quota unavailable");
});

test("renders the approved mobile Control Tower", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openControlTower(page);

  await expect(page.locator("main")).toBeVisible();
  await expect(page.locator("#today-jobs")).toBeVisible();
});
