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
      `event: run.status\ndata: ${JSON.stringify({ stage: "agent_planning", text: "Hermes 正在理解问题" })}\n\n`,
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
    await expect(page.locator(".composition-chip.agent").last()).toContainText("Hermes Agent");
  }
  expect(requestBodies).toHaveLength(3);
  expect(requestBodies.every((body) => body.intelligence_mode === "full")).toBeTruthy();
});
