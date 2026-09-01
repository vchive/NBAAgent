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
  await expect(page.locator("#history-status")).toContainText("正在拉取今日赛事");
  await expect(page.locator("#highlights-empty.is-loading")).toHaveCount(0);
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

test("a fast cached history response does not flash loading", async ({ page }) => {
  await page.route("**/api/v1/highlights/recent**", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 50));
    await route.continue();
  });
  await page.goto("/");
  await page.locator('[data-highlight-mode="history"]').click();
  await page.waitForTimeout(100);
  await expect(page.locator("#history-status")).not.toContainText("正在拉取");
  await expect(page.locator("#highlights-empty.is-loading")).toHaveCount(0);
  await expect(page.locator("#game-list .game-list-card")).toHaveCount(5);
});

test("announces one visible loading state while fetching slow history", async ({ page }) => {
  await page.route("**/api/v1/highlights/recent**", async (route) => {
    // Keep the response beyond the 250 ms anti-flicker threshold long enough
    // for browser/CI polling to observe the intentionally visible state.
    await new Promise((resolve) => setTimeout(resolve, 800));
    await route.continue();
  });
  await page.goto("/");
  await page.locator('[data-highlight-mode="history"]').click();
  await expect(page.locator("#history-status")).toContainText("正在拉取");
  await expect(page.locator("#highlights-empty.is-loading")).toHaveCount(0);
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

test("recommends winner analysis from the final score for either home or away winner", async ({ page }) => {
  await page.goto("/");
  await page.locator("#message-input").fill("你好");
  await page.locator("#message-input").press("Enter");
  await expect(page.locator("#recommendations")).toBeVisible();
  await page.locator('[data-highlight-mode="history"]').click();
  const cards = page.locator("#game-list .game-list-card");
  await expect(cards).toHaveCount(5);

  // G3: away Celtics 112, home Thunder 101.
  await cards.nth(3).click();
  await expect(page.locator("#recommendation-list")).toContainText(
    "凯尔特人 为什么能赢下这场比赛？",
  );
  await expect(page.locator("#recommendation-list")).not.toContainText(
    "雷霆 为什么能赢下这场比赛？",
  );

  // G2: away Celtics 99, home Thunder 107.
  await cards.nth(4).click();
  await expect(page.locator("#recommendation-list")).toContainText(
    "雷霆 为什么能赢下这场比赛？",
  );
  await expect(page.locator("#recommendation-list")).not.toContainText(
    "凯尔特人 为什么能赢下这场比赛？",
  );
});
