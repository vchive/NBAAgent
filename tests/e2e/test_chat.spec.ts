import { expect, test } from "@playwright/test";

test("streams a question and keeps the input usable", async ({ page }) => {
  await page.goto("/");
  const input = page.locator("#message-input");
  await input.fill("2025-26 总决赛 G4 谁得分最高？");
  await input.press("Enter");
  await expect(page.locator(".dynamic-message.assistant-message").last()).toContainText("32");
  await expect(page.locator("#stream-status")).toBeHidden();
  await expect(input).toBeEnabled();
  await expect(page.locator("#recommendations")).toBeVisible();
  await expect(page.locator(".recommendation-button")).toHaveCount(3);
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

test("forwards the selected highlights card as chat context", async ({ page }) => {
  let selectedGameId: unknown = null;
  await page.route("**/api/v1/chat/stream", async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    selectedGameId = body.selected_game_id;
    const requestId = "22222222-2222-4222-8222-222222222222";
    const sessionId = String(body.session_id);
    const answer = "开赛时间：2026-06-12 09:30（北京时间）。";
    const completed = {
      request_id: requestId,
      session_id: sessionId,
      status: "completed",
      answer_markdown: answer,
      blocks: [{ type: "text", content: answer }],
      as_of_beijing: null,
      evidence_state: "verified",
      corrections: [],
      follow_up: null,
      latency_ms: 10,
    };
    const sse = [
      `event: run.started\ndata: ${JSON.stringify({ request_id: requestId, session_id: sessionId })}\n\n`,
      `event: message.completed\ndata: ${JSON.stringify(completed)}\n\n`,
    ].join("");
    await route.fulfill({ status: 200, contentType: "text/event-stream", body: sse });
  });

  await page.goto("/");
  await expect(page.locator("#game-list .game-list-card")).toHaveCount(3);
  await page.locator("#game-list .game-list-card").nth(1).click();
  await page.locator("#message-input").fill("这场比赛什么时候打的？");
  await page.locator("#message-input").press("Enter");
  await expect(page.locator(".dynamic-message.assistant-message").last()).toContainText("09:30");
  expect(selectedGameId).toBe("2026-demo-den-gsw");
});

test("keeps the first replay card bound to a venue question", async ({ page }) => {
  let selectedGameId: unknown = null;
  await page.route("**/api/v1/chat/stream", async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    selectedGameId = body.selected_game_id;
    const requestId = "44444444-4444-4444-8444-444444444444";
    const sessionId = String(body.session_id);
    const answer = "这场比赛（雷霆 vs 凯尔特人）的场馆暂未在公开记录中提供。";
    const completed = {
      request_id: requestId,
      session_id: sessionId,
      status: "completed",
      answer_markdown: answer,
      blocks: [{ type: "warning", content: answer }],
      as_of_beijing: null,
      evidence_state: "partial",
      corrections: [],
      follow_up: null,
      latency_ms: 10,
    };
    const sse = [
      `event: run.started\ndata: ${JSON.stringify({ request_id: requestId, session_id: sessionId })}\n\n`,
      `event: message.completed\ndata: ${JSON.stringify(completed)}\n\n`,
    ].join("");
    await route.fulfill({ status: 200, contentType: "text/event-stream", body: sse });
  });

  await page.goto("/");
  await expect(page.locator("#game-list .game-list-card")).toHaveCount(3);
  await page.locator("#game-list .game-list-card").first().click();
  await page.locator("#message-input").fill("这场比赛在哪儿举办的？");
  await page.locator("#message-input").press("Enter");
  await expect(page.locator(".dynamic-message.assistant-message").last()).toContainText("雷霆");
  await expect(page.locator(".dynamic-message.assistant-message").last()).toContainText("凯尔特人");
  expect(selectedGameId).toBe("2026-finals-g4");
});

test("explicit game mention supersedes a stale selected card for follow-ups", async ({ page }) => {
  const selectedGameIds: unknown[] = [];
  await page.route("**/api/v1/chat/stream", async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    selectedGameIds.push(body.selected_game_id);
    const requestId = `33333333-3333-4333-8333-${String(selectedGameIds.length).padStart(12, "0")}`;
    const sessionId = String(body.session_id);
    const answer = String(body.message).includes("G4")
      ? "杰伦·布朗得到 32 分。"
      : "这场比赛是雷霆对凯尔特人。";
    const completed = {
      request_id: requestId,
      session_id: sessionId,
      status: "completed",
      answer_markdown: answer,
      blocks: [{ type: "text", content: answer }],
      as_of_beijing: null,
      evidence_state: "verified",
      corrections: [],
      follow_up: null,
      latency_ms: 10,
    };
    const sse = [
      `event: run.started\ndata: ${JSON.stringify({ request_id: requestId, session_id: sessionId })}\n\n`,
      `event: message.completed\ndata: ${JSON.stringify(completed)}\n\n`,
    ].join("");
    await route.fulfill({ status: 200, contentType: "text/event-stream", body: sse });
  });

  await page.goto("/");
  await expect(page.locator("#game-list .game-list-card")).toHaveCount(3);
  // Pick the second card first, then explicitly ask about G4.  The next
  // pronoun turn must follow G4 rather than the stale second-card selection.
  await page.locator("#game-list .game-list-card").nth(1).click();
  const input = page.locator("#message-input");
  await input.fill("2025-26 总决赛 G4 谁得分最高？");
  await input.press("Enter");
  await expect(page.locator(".dynamic-message.assistant-message").last()).toContainText("32");
  await input.fill("这场比赛谁打谁");
  await input.press("Enter");
  await expect(page.locator(".dynamic-message.assistant-message").last()).toContainText("雷霆对凯尔特人");
  expect(selectedGameIds).toEqual(["2026-finals-g4", "2026-finals-g4"]);
});

test("full intelligence acceptance prompts render Agent provenance", async ({ page }) => {
  const requestBodies: Array<Record<string, unknown>> = [];
  await page.route("**/healthz", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        status: "ok",
        version: "v1",
        mode: "hybrid",
        capabilities: { full_intelligence: true, web_search: true },
        dependencies: { session_store: "ok", cache: "ok", hermes: "ok", auth: "ok", web_search: "enabled" },
      }),
    });
  });
  await page.route("**/api/v1/chat/stream", async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    requestBodies.push(body);
    const question = String(body.message || "");
    const answer = question === "nihao"
      ? "您好！可以问我 NBA 赛程、球员和比赛。"
      : "北京时间 2026-08-31 至 2026-09-06 没有查到 NBA 比赛。";
    const requestId = "11111111-1111-4111-8111-111111111111";
    const sessionId = String(body.session_id);
    const completed = {
      request_id: requestId,
      session_id: sessionId,
      status: "completed",
      answer_markdown: answer,
      blocks: [{ type: "text", content: answer }],
      as_of_beijing: null,
      evidence_state: "none",
      corrections: [],
      follow_up: null,
      latency_ms: 120,
      composition: { mode: "agent", status: "used", latency_ms: 100 },
    };
    const sse = [
      `event: run.started\ndata: ${JSON.stringify({ request_id: requestId, session_id: sessionId })}\n\n`,
      `event: run.status\ndata: ${JSON.stringify({ stage: "agent_planning", text: "正在理解问题" })}\n\n`,
      `event: message.delta\ndata: ${JSON.stringify({ text: answer })}\n\n`,
      `event: message.completed\ndata: ${JSON.stringify(completed)}\n\n`,
    ].join("");
    await route.fulfill({ status: 200, contentType: "text/event-stream", body: sse });
  });

  await page.goto("/");
  const toggle = page.locator("#intelligence-mode");
  await expect(toggle).toBeEnabled();
  await toggle.check();
  const input = page.locator("#message-input");
  for (const prompt of ["nihao", "下周有比赛买", "下周有比赛吗"]) {
    await input.fill(prompt);
    await input.press("Enter");
    await expect(page.locator(".dynamic-message.assistant-message").last()).toContainText(
      prompt === "nihao" ? "您好" : "2026-09-06"
    );
    await expect(page.locator(".composition-chip.agent").last()).toContainText("智能分析");
    await expect(page.locator("body")).not.toContainText("Hermes");
  }
  expect(requestBodies).toHaveLength(3);
  expect(requestBodies.every((body) => body.intelligence_mode === "full")).toBeTruthy();
  const logicalSessionId = String(requestBodies[0].session_id);
  expect(requestBodies.every((body) => body.session_id === logicalSessionId)).toBeTruthy();

  // Refresh continues the same application/Agent logical session. Starting
  // a new chat rotates it and clears any selected replay card.
  await page.reload();
  await expect(page.locator("#message-input")).toBeVisible();
  expect(await page.evaluate(() => window.sessionStorage.getItem("courtside-demo-session-v1")))
    .toBe(logicalSessionId);
  await page.locator('[data-highlight-mode="history"]').click();
  await expect(page.locator("#game-list .game-list-card").first()).toHaveAttribute("aria-pressed", "true");
  await page.locator("#new-session").click();
  const newSessionId = await page.evaluate(() => window.sessionStorage.getItem("courtside-demo-session-v1"));
  expect(newSessionId).not.toBe(logicalSessionId);
  await expect(page.locator("#game-list .game-list-card").first()).toHaveAttribute("aria-pressed", "false");
});
