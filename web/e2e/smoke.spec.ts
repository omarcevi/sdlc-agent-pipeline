import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";

const ORIGIN = "http://localhost:4173";
const TRAP = "md-005-multi-flash-r1-20261001T062611Z";

test("plays the trap replay to the end", async ({ page }) => {
  const problems: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error") problems.push(`console: ${m.text()}`);
  });
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
  page.on("request", (r) => {
    if (!r.url().startsWith("data:") && new URL(r.url()).origin !== ORIGIN) problems.push(`request: ${r.url()}`);
  });

  await page.goto("/#/");
  await page.locator(`a[href="#/run/${TRAP}"]`).first().click();
  await expect(page.getByTestId("node-planner")).toBeVisible();
  await page.getByLabel("Speed").selectOption("50");
  await page.getByRole("button", { name: "Play" }).click();

  await expect(page.getByTestId("outcome")).toContainText("Declined · correct", { timeout: 10_000 });
  await expect(page.getByTestId("node-report_failure")).toHaveAttribute("data-state", "end");

  // The feed followed the run: it ends scrolled to (near) its bottom.
  const feed = page.getByTestId("feed-scroll");
  await expect
    .poll(() => feed.evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight), { timeout: 3000 })
    .toBeLessThan(40);

  expect(problems).toEqual([]);
});

test("the build uses only relative asset paths", () => {
  const html = readFileSync("dist/index.html", "utf8");
  const refs = [...html.matchAll(/\b(?:src|href)="([^"]*)"/g)].map((m) => m[1]);
  expect(refs.length).toBeGreaterThan(0);
  for (const ref of refs) expect(ref.startsWith("/"), ref).toBe(false);
});
