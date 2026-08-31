import { expect, test } from "@playwright/test";

test("shows all games and switches to an available history date", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("#connection-label")).toHaveText("API 就绪");
  await expect(page.locator("#game-list .game-list-card")).toHaveCount(3);

  await page.locator('[data-highlight-mode="history"]').click();
  await page.locator("#highlight-date-trigger").click();
  await expect(page.locator("#highlight-calendar")).toBeVisible();
  const available = page.locator('.calendar-day[data-availability="available"]');
  await expect(available).not.toHaveCount(0);
  await expect(page.locator('.calendar-day[data-availability="empty"]').first()).toBeDisabled();
  await available.first().click();
  await expect(page.locator("#highlights-title")).toContainText("历史回顾");
});

test("loads selected game final data and replay rows", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("#game-list .game-list-card")).toHaveCount(3);
  await expect(page.locator("#away-score")).toHaveText("104");
  await expect(page.locator("#home-score")).toHaveText("108");
  await expect(page.locator("#pbp-list .pbp-event")).toHaveCount(6);
  await expect(page.locator("#event-count")).toHaveText("06 EVENTS");
});

test("a game without PBP shows a truthful empty replay state", async ({ page }) => {
  await page.goto("/");
  await page.locator("#game-list .game-list-card").nth(1).click();
  await expect(page.locator("#pbp-list .pbp-empty")).toContainText("暂无可用的逐回合记录");
});
