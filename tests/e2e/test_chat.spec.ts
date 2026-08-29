import { expect, test } from "@playwright/test";

test("streams a question and keeps the input usable", async ({ page }) => {
  await page.goto("/");
  const input = page.locator("#message-input");
  await input.fill("2025-26 总决赛 G4 谁得分最高？");
  await input.press("Enter");
  await expect(page.locator(".dynamic-message.assistant-message").last()).toContainText("32");
  await expect(page.locator("#stream-status")).toBeHidden();
  await expect(input).toBeEnabled();
});

test("supports cancellation and a follow-up in the same session", async ({ page }) => {
  await page.goto("/");
  const input = page.locator("#message-input");
  await input.fill("2025-26 总决赛 G4 谁得分最高？");
  await input.press("Enter");
  await expect(page.locator(".dynamic-message.assistant-message").last()).toContainText("32");
  await input.fill("那场最后五秒发生了什么？");
  await input.press("Enter");
  await expect(page.locator(".dynamic-message.assistant-message").last()).toContainText("2 个回合");
});
