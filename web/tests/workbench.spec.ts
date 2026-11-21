import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test("queue filters, blockers and keyboard navigation preserve server order", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Screening queue" }),
  ).toBeVisible();
  const rows = page.locator(".queue-table tbody tr");
  await expect(rows).toHaveCount(11);
  await expect(rows.first()).toContainText("IBRAHIM SHUAIBU");
  await page.keyboard.press("j");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/alerts\/A-DBA307$/);
  await page.getByRole("link", { name: "← Screening queue" }).click();
  await page.getByLabel(/^Status/).selectOption("cleared");
  await expect(rows).toHaveCount(2);
  await page.getByLabel(/^Status/).selectOption("");
  await page.getByLabel("Practice labelled").click();
  await expect(page.getByLabel("Practice labelled")).toBeChecked();
  await expect(rows).toHaveCount(4);
  // The filter lives in the URL, so the box re-renders a moment after the click.
  await page.getByLabel("Practice labelled").click();
  await expect(page.getByLabel("Practice labelled")).not.toBeChecked();
  await page.getByText("What is keeping the queue open?").click();
  await page
    .getByRole("button", {
      name: "Name and birth date agree, countries differ 12",
    })
    .click();
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("Markus Müller");
  await page.getByRole("button", { name: "Clear blocker filter" }).click();
  await page.getByLabel("Search queue").fill("not-a-person");
  await expect(page.getByText("No alerts match these filters.")).toBeVisible();
  await page.getByLabel("Search queue").fill("Markus");
  await expect(rows).toHaveCount(1);
  await page.screenshot({
    path: "test-results/queue-light.png",
    fullPage: true,
  });
});

test("deep link, source drawer, investigator, chat, note and analyst override", async ({
  page,
  context,
}) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/alerts/A-0DF328/H-ADF453");
  await expect(
    page.getByRole("heading", { name: "Markus Müller" }),
  ).toBeVisible();
  await expect(page.locator(".hit-tab.active")).toContainText("Marcus Miller");
  await page
    .getByRole("button", { name: "Open record KYC-A932A4" })
    .first()
    .click();
  const drawer = page.getByRole("dialog", { name: "Source record" });
  await expect(
    drawer.getByText("Identity check", { exact: true }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(drawer).not.toBeVisible();
  await page.getByRole("button", { name: "Investigate", exact: true }).click();
  await expect(
    page.getByText(
      "Ask the customer whether they have lived or worked in the United States.",
      { exact: true },
    ),
  ).toBeVisible();
  await page
    .getByLabel("Question", { exact: true })
    .fill("Why does country not clear this hit?");
  await page.getByRole("button", { name: "Send question" }).click();
  await expect(
    page.getByRole("log", { name: "Casebook conversation" }),
  ).toContainText("both agree with the profile.");
  await page.getByRole("button", { name: "Draft note" }).click();
  const draft = page.getByLabel("Case note draft");
  await expect(draft).toHaveValue(/H-ADF453/);
  await draft.fill("Edited case note with KYC-A932A4.");
  await page
    .locator("section")
    .filter({
      has: page.getByRole("heading", { name: "Case note", exact: true }),
    })
    .getByRole("button", { name: "Copy", exact: true })
    .click();
  await expect
    .poll(() => page.evaluate(() => navigator.clipboard.readText()))
    .toBe("Edited case note with KYC-A932A4.");
  const review = page.locator(".review-panel");
  await review
    .getByLabel(/^Decision/)
    .selectOption("SAME_ENTITY");
  await review.getByLabel("Reason code").selectOption("NAME_DOB_MATCH");
  await review
    .getByLabel("Review comment")
    .fill("Customer confirmed a US address.");
  await expect(
    review.getByText("Overrides engine decision.", { exact: true }),
  ).toBeVisible();
  await review.getByRole("button", { name: "Record analyst decision" }).click();
  await expect(review.getByRole("status")).toContainText("Review recorded");
  await expect(review.locator(".audit-entry").first()).toContainText(
    "Customer confirmed a US address.",
  );
  await expect(page.getByLabel("Engine decision")).toContainText("Review");
  await page.screenshot({
    path: "test-results/case-light.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
});

test("candidate selection, missing signals, unavailable records and replay honesty", async ({
  page,
}) => {
  await page.goto("/alerts/A-3983D4");
  const candidates = page.locator(".candidate-select");
  await expect(candidates).toHaveCount(4);
  await candidates.nth(1).click();
  await expect(candidates.nth(1)).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "Investigate", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText(
    "available for H-ADF453 only",
  );
  await page.goto("/alerts/A-41F2A9");
  await expect(page.getByText("Not scored for this hit")).toBeVisible();
  await page.goto("/alerts/A-0DF328/H-ADF453");
  await page
    .getByRole("button", { name: "Search", exact: false })
    .first()
    .click();
  const palette = page.getByRole("dialog", { name: "Search Casebook" });
  await palette.getByRole("searchbox").fill("Pierre");
  await palette.getByRole("button", { name: "Open record CUS-000690" }).click();
  await expect(
    page.getByRole("dialog", { name: "Source record" }).getByRole("alert"),
  ).toContainText("not included in the fixture sample");
});

test("QA matrix and every available case row navigate to the correct hit", async ({
  page,
}) => {
  await page.goto("/qa");
  await expect(
    page.getByRole("heading", { name: "Decision quality" }),
  ).toBeVisible();
  await expect(
    page
      .getByRole("region", { name: "Practice label comparison" })
      .locator("tbody tr"),
  ).toHaveCount(4);
  await expect(
    page
      .getByRole("region", { name: "Engine versus second opinion matrix" })
      .locator("tbody tr"),
  ).toHaveCount(3);
  await expect(
    page
      .getByRole("region", { name: "Second opinion disagreements" })
      .locator("tbody tr"),
  ).toHaveCount(6);
  await page.screenshot({ path: "test-results/qa-light.png", fullPage: true });
  await page
    .getByRole("link", { name: "Raković, Aleksandar →", exact: true })
    .first()
    .click();
  await expect(page).toHaveURL(/A-AE9222\/H-622EDD$/);
});

test("command palette keyboard shortcut searches customers and alerts", async ({
  page,
}) => {
  await page.goto("/");
  await page.keyboard.press("Meta+k");
  const dialog = page.getByRole("dialog", { name: "Search Casebook" });
  await dialog.getByRole("searchbox").fill("Markus");
  await expect(
    dialog.getByRole("button", { name: "Open record CUS-117438" }),
  ).toBeVisible();
  await dialog.getByRole("link", { name: /Markus Müller/ }).click();
  await expect(page).toHaveURL(/alerts\/A-0DF328$/);
  await expect(dialog).not.toBeVisible();
});

test("light, dark and narrow layouts are accessible and keep tables contained", async ({
  page,
}) => {
  for (const route of [
    "/",
    "/alerts/A-0DF328/H-ADF453",
    "/qa",
    "/benchmarks",
  ]) {
    await page.goto(route);
    await expect(page.locator(".loading")).toHaveCount(0);
    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
      .analyze();
    expect(results.violations).toEqual([]);
  }
  // The app is dark unless the user chose light, so the pass above was the dark theme.
  await page.evaluate(() => localStorage.setItem("casebook-theme", "light"));
  for (const route of ["/", "/qa", "/benchmarks"]) {
    await page.goto(route);
    await expect(page.locator(".loading")).toHaveCount(0);
    expect(
      (await new AxeBuilder({ page }).withTags(["wcag2aa"]).analyze())
        .violations,
    ).toEqual([]);
  }
  await page.goto("/alerts/A-0DF328/H-ADF453");
  await expect(page.locator(".loading")).toHaveCount(0);
  expect(
    (await new AxeBuilder({ page }).withTags(["wcag2aa"]).analyze()).violations,
  ).toEqual([]);
  await page.screenshot({ path: "test-results/case-dark.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: "test-results/case-mobile.png",
    fullPage: true,
  });
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth,
  );
  expect(overflow).toBe(false);
  const comparison = page.getByRole("region", {
    name: "Customer and watchlist comparison",
  });
  await expect(comparison).toBeVisible();
  expect(
    await comparison.evaluate((el) => el.scrollWidth > el.clientWidth),
  ).toBe(true);
});
