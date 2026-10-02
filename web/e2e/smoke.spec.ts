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

const MD1 = "md-001-multi-flash-r1-20261001T062611Z";
const SR3 = "sr-003-multi-flash-r1-20261001T062611Z";

/** The last step's time, which is where the replay ends. */
function endOf(runId: string): number {
  const replay = JSON.parse(readFileSync(`public/replays/${runId}.json`, "utf8")) as { steps: { t: number }[] };
  return Math.max(...replay.steps.map((s) => s.t));
}

test("every tab renders at the end of a resolved run, and a cap stop marks its node", async ({ page }) => {
  const problems: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error") problems.push(`console: ${m.text()}`);
  });
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));

  await page.goto(`/#/run/${MD1}?t=${endOf(MD1)}`);
  await expect(page.getByTestId("outcome")).toBeVisible();
  for (const name of ["Issue", "Plan", "Agent summary", "Diff", "Tests", "Review"]) {
    await page.getByRole("tab", { name }).click();
    await expect(page.getByRole("tab", { name })).toHaveAttribute("aria-selected", "true");
    await expect(page.getByRole("tabpanel")).toBeVisible();
  }

  await page.goto(`/#/run/${SR3}?t=${endOf(SR3)}`);
  await expect(page.getByTestId("node-coder")).toHaveAttribute("data-state", "stopped");

  expect(problems).toEqual([]);
});

test("the build uses only relative asset paths", () => {
  const html = readFileSync("dist/index.html", "utf8");
  const refs = [...html.matchAll(/\b(?:src|href)="([^"]*)"/g)].map((m) => m[1]);
  expect(refs.length).toBeGreaterThan(0);
  for (const ref of refs) expect(ref.startsWith("/"), ref).toBe(false);
});
