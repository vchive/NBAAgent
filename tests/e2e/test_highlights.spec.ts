import { expect, test } from "@playwright/test";

test("resolves today's Beijing date before painting the highlights rail", async ({ page }) => {
  const today = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
  let requestedDate: string | null = null;
  await page.route("**/healthz", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: "ok",
        version: "v1",
        mode: "hybrid",
        dependencies: { session_store: "ok", cache: "ok", hermes: "ok" },
        capabilities: { full_intelligence: true },
      }),
    });
  });
  await page.route("**/api/v1/highlights?*", async (route) => {
    const url = new URL(route.request().url());
    requestedDate = url.searchParams.get("date");
    await new Promise((resolve) => setTimeout(resolve, 1200));
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        date: today,
        timezone: "Asia/Shanghai",
        games: [],
        as_of_beijing: null,
        evidence_state: "none",
      }),
    });
  });

  await page.goto("/");
  await expect(page.locator("#highlights-empty.is-loading")).toBeVisible();
  await expect(page.locator("#highlights-empty")).toContainText("正在拉取今日赛事");
  await expect(page.locator("#featured-game")).toBeHidden();
  await expect(page.locator("#highlights-empty")).toContainText("今天没有 NBA 比赛");
  expect(requestedDate).toBe(today);
  await expect(page.locator("#day-divider")).toContainText(today.replaceAll("-", "/"));
  await expect(page.locator("#highlights-empty")).not.toContainText("2026-06-12");
});

test("shows recent five games and supports a custom history range", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("#connection-label")).toHaveText("API 就绪");
  await expect(page.locator("#game-list .game-list-card")).toHaveCount(3);

  await page.locator('[data-highlight-mode="history"]').click();
  await expect(page.locator("#history-controls")).toBeVisible();
  await expect(page.locator("#game-list .game-list-card")).toHaveCount(5);
  await expect(page.locator("#highlights-title")).toContainText("精彩回顾");
  await expect(page.locator("#games-section-title")).toContainText("最近 5 场比赛");

  await page.locator("#history-custom").click();
  await expect(page.locator("#history-range-picker")).toBeVisible();
  await page.locator("#history-from").fill("2026-06-06");
  await page.locator("#history-to").fill("2026-06-12");
  await page.locator("#history-range-apply").click();
  await expect(page.locator("#game-list .game-list-card")).toHaveCount(6);
  await expect(page.locator("#highlights-title")).toContainText("2026/06/06");
});

test("announces a visible loading state while fetching history", async ({ page }) => {
  await page.route("**/api/v1/highlights/recent**", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 300));
    await route.continue();
  });
  await page.goto("/");
  await page.locator('[data-highlight-mode="history"]').click();
  await expect(page.locator("#history-status")).toContainText("正在拉取");
  await expect(page.locator("#highlights-empty.is-loading")).toBeVisible();
  await expect(page.locator("#game-list .game-list-card")).toHaveCount(5);
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
