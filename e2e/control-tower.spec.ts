import { expect, test } from "@chromatic-com/playwright";

test("loads the Control Tower shell", async ({ page }) => {
  await page.goto("/");

  await expect(page).toHaveTitle("Josh 2.0 | Control Tower");
  await expect(page.locator("#root")).not.toBeEmpty();
});
