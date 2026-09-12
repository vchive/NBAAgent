import { expect, test } from "@playwright/test";

test("starts in roaming mode without requesting today's schedule", async ({ page }) => {
  let highlightsRequests = 0;
  await page.route("**/healthz", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: "ok",
        version: "v1",
        experience: "public",
        capabilities: { intelligent_analysis: true, default_intelligent_analysis: true },
      }),
    });
  });
  await page.route("**/api/v1/highlights?*", async (route) => {
    highlightsRequests += 1;
    await route.continue();
  });

  await page.goto("/");
  await expect(page.locator('[data-highlight-mode="roaming"]')).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("#highlights-title")).toHaveText("漫游模式");
  await expect(page.locator("#highlights-empty")).toContainText("漫游模式不绑定具体比赛");
  await expect(page.locator("#featured-game")).toBeHidden();
  await expect(page.locator("#game-list .game-list-card")).toHaveCount(0);
  await expect(page.locator("#highlights-title")).not.toContainText("今日赛事");
  await expect(page.locator("#games-section-title")).not.toContainText("今日赛事");
  expect(highlightsRequests).toBe(0);
});

test("shows recent five games and supports a custom history range", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("#connection-label")).toHaveText("API 就绪");

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

test("can locate a date, including an empty day, from event drill-down", async ({ page }) => {
  await page.goto("/");
  await page.locator('[data-highlight-mode="history"]').click();
  await expect(page.locator("#game-list .game-list-card")).toHaveCount(5);
  await page.locator("#highlight-date-trigger").click();
  await expect(page.locator("#highlight-calendar")).toBeVisible();
  const emptyDay = page.locator('#calendar-grid [data-date="2026-06-07"]');
  await expect(emptyDay).toBeEnabled();
  await emptyDay.click();
  await expect(page.locator("#highlights-title")).toContainText("2026/06/07");
  await expect(page.locator("#highlights-empty")).toContainText("2026/06/07");
  await expect(page.locator("#game-list .game-list-card")).toHaveCount(0);
});

test("keeps public and snapshot provenance separate in a mixed recent list", async ({ page }) => {
  await page.route("**/healthz", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: "ok",
        version: "v1",
        experience: "public",
        capabilities: { intelligent_analysis: true, default_intelligent_analysis: true },
      }),
    });
  });
  await page.route("**/api/v1/highlights/recent**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        timezone: "Asia/Shanghai",
        from: "2026-06-01",
        to: "2026-08-31",
        games: [
          {
            game_id: "public-1",
            start_utc: "2026-08-30T12:00:00Z",
            home_name: "湖人",
            home_abbreviation: "LAL",
            away_name: "勇士",
            away_abbreviation: "GSW",
            status: "final",
            home_score: 110,
            away_score: 105,
            data_origin: "public",
          },
          {
            game_id: "snapshot-1",
            start_utc: "2026-06-12T01:30:00Z",
            home_name: "凯尔特人",
            home_abbreviation: "BOS",
            away_name: "雷霆",
            away_abbreviation: "OKC",
            status: "final",
            home_score: 108,
            away_score: 104,
            data_origin: "demo_snapshot",
          },
        ],
        as_of_beijing: "2026-08-31 20:00",
        evidence_state: "partial",
        data_origin: "mixed",
      }),
    });
  });

  await page.goto("/");
  await page.locator('[data-highlight-mode="history"]').click();
  const cards = page.locator("#game-list .game-list-card");
  await expect(cards).toHaveCount(2);
  await expect(cards.nth(0).locator(".game-list-card-foot")).not.toContainText("DEMO");
  await expect(cards.nth(1).locator(".game-list-card-foot")).toContainText("DEMO");
});

test("does not paint fixture games when a public history request loses the network", async ({ page }) => {
  await page.route("**/healthz", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: "ok",
        version: "v1",
        experience: "public",
        capabilities: { intelligent_analysis: true, default_intelligent_analysis: true },
      }),
    });
  });
  await page.route("**/api/v1/highlights/recent**", async (route) => route.abort("failed"));

  await page.goto("/");
  await page.locator('[data-highlight-mode="history"]').click();
  await expect(page.locator("#history-status")).toContainText("连接暂时不可用");
  await expect(page.locator("#history-status")).toContainText("重试");
  await expect(page.locator("#game-list .game-list-card")).toHaveCount(0);
  await expect(page.locator("#highlights-empty")).toContainText("不可用");
  await expect(page.locator("#highlights-empty")).not.toContainText("2026/06/12");
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
  // The highlights rail is browse-only by default. Explicitly enter the
  // event-drill scope before asserting HUD/PBP data.
  await expect(page.locator("#conversation-scope")).toHaveText("漫游模式");
  await page.locator('[data-highlight-mode="history"]').click();
  await expect(page.locator("#game-list .game-list-card")).toHaveCount(5);
  await page.locator("#game-list .game-list-card").first().click();
  await expect(page.locator("#conversation-scope")).toHaveText("赛事下钻");
  await expect(page.locator("#away-score")).toHaveText("104");
  await expect(page.locator("#home-score")).toHaveText("108");
  await expect(page.locator("#pbp-list .pbp-event")).toHaveCount(6);
  await expect(page.locator("#event-count")).toHaveText("06 EVENTS");
  await expect(page.locator("#score-clock")).toHaveText("00:00 · FINAL");
  await expect(page.locator("#selected-play-text")).toContainText("0:00.0 · Q4");

  await page.locator("#pbp-list .pbp-event").nth(4).click();
  await expect(page.locator("#score-clock")).toHaveText("0:05.0 · Q4");
  await page.locator("#pbp-list .pbp-event").last().click();
  await expect(page.locator("#score-clock")).toHaveText("00:00 · FINAL");
});

test("derives regulation quarter scores from complete PBP period endpoints", async ({ page }) => {
  const game = {
    game_id: "public-quarter-score-game",
    start_utc: "2026-06-14T00:30:00Z",
    home_name: "马刺",
    home_abbreviation: "SAS",
    away_name: "尼克斯",
    away_abbreviation: "NYK",
    status: "final",
    home_score: 99,
    away_score: 101,
    series_game_number: 5,
    data_origin: "public",
  };
  await page.route("**/api/v1/highlights/recent**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        timezone: "Asia/Shanghai",
        from: "2026-06-14",
        to: "2026-06-14",
        games: [game],
        as_of_beijing: "2026-06-14 11:30",
        evidence_state: "verified",
        data_origin: "public",
      }),
    });
  });
  await page.route("**/api/v1/highlights/public-quarter-score-game/detail?*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        game,
        leaders: [],
        plays: [
          { period: 1, clock: "00:12.0", team: "NYK", action: "罚球命中", away_score: 23, home_score: 20 },
          { period: 2, clock: "12:00.0", team: "SAS", action: "第二节开始", away_score: 25, home_score: 20 },
          { period: 2, clock: "00:09.0", team: "NYK", action: "跳投命中", away_score: 48, home_score: 50 },
          { period: 3, clock: "12:00.0", team: "NYK", action: "第三节开始", away_score: 48, home_score: 52 },
          { period: 3, clock: "00:05.0", team: "SAS", action: "罚球命中", away_score: 74, home_score: 75 },
          { period: 4, clock: "12:00.0", team: "NYK", action: "第四节开始", away_score: 76, home_score: 75 },
          { period: 4, clock: "00:02.0", team: "NYK", player_name: "杰伦·布伦森", action: "罚球命中", away_score: 101, home_score: 99 },
        ],
        as_of_beijing: "2026-06-14 11:30",
        evidence_state: "verified",
        data_origin: "public",
      }),
    });
  });

  await page.goto("/");
  await page.locator('[data-highlight-mode="history"]').click();
  await page.locator("#game-list .game-list-card").click();

  await expect(page.locator('[data-quarter="Q1"] [data-quarter-score]')).toHaveText("25–20");
  await expect(page.locator('[data-quarter="Q2"] [data-quarter-score]')).toHaveText("23–32");
  await expect(page.locator('[data-quarter="Q3"] [data-quarter-score]')).toHaveText("28–23");
  await expect(page.locator('[data-quarter="Q4"] [data-quarter-score]')).toHaveText("25–24");
  await expect(page.locator("#score-clock")).toHaveText("00:00 · FINAL");
  await expect(page.locator("#selected-play-text")).toContainText("00:02.0 · Q4");
});

test("can explicitly leave event drill-down and return to roaming", async ({ page }) => {
  await page.goto("/");
  await page.locator('[data-highlight-mode="history"]').click();
  await page.locator("#game-list .game-list-card").first().click();
  await expect(page.locator("#conversation-scope")).toHaveText("赛事下钻");
  await expect(page.locator("#conversation-scope")).toHaveAttribute("aria-pressed", "true");
  await page.locator("#conversation-scope").click();
  await expect(page.locator("#conversation-scope")).toHaveText("漫游模式");
  await expect(page.locator("#conversation-scope")).toHaveAttribute("aria-pressed", "false");
  await expect(page.locator("#away-score")).toHaveText("—");
  await expect(page.locator("#game-list .game-list-card").first()).toHaveAttribute("aria-pressed", "false");
});

test("a game without PBP shows a truthful empty replay state", async ({ page }) => {
  await page.goto("/");
  await page.locator('[data-highlight-mode="history"]').click();
  await page.locator("#game-list .game-list-card").nth(1).click();
  await expect(page.locator("#pbp-list .pbp-empty")).toContainText("暂无可用的逐回合记录");
  await expect(page.locator("[data-quarter-score]")).toHaveText(["—", "—", "—", "—"]);
  await expect(page.locator("#score-clock")).toHaveText("00:00 · FINAL");
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
