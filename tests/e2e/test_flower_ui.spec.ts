import { expect, test } from "@playwright/test";

/**
 * The default page is intentionally domain-neutral at the transport layer,
 * but its public experience must be recognisably the flower assistant.  Keep
 * this smoke test focused on what a user can see before sending a question.
 */
test("opens in flower roaming mode without legacy sports content", async ({ page }) => {
  await page.goto("/");

  await expect(page).toHaveTitle(/种花 Agent/i);
  await expect(page.locator(".chat-panel h1")).toContainText("种花 Agent");
  await expect(page.locator("#message-input")).toHaveAttribute(
    "placeholder",
    /花卉种植/,
  );
  await expect(page.locator("#conversation-scope")).toHaveText("漫游模式");
  await expect(page.locator("#prompt-list")).toContainText("北阳台适合种什么花");
  await expect(page.locator(".right-rail")).toBeHidden();
  expect(await page.locator(".legacy-game-ui:visible").count()).toBe(0);

  const visibleText = await page.locator("body").innerText();
  expect(visibleText).not.toMatch(/\bNBA\b|篮球|总决赛|球员|球队|比分/iu);
});

test("shows a friendly quota notice without leaking upstream details", async ({ page }) => {
  await page.route("**/api/v1/chat/stream", async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    const requestId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
    const sessionId = String(body.session_id || "");
    const completed = {
      request_id: requestId,
      session_id: sessionId,
      status: "completed",
      answer_markdown: "绣球的表土干燥后再浇透。",
      blocks: [{ type: "text", content: "绣球的表土干燥后再浇透。" }],
      evidence_state: "partial",
      data_origin: "local_knowledge",
      notices: [
        { code: "INTELLIGENCE_QUOTA_EXHAUSTED", message: "internal model vendor billing detail" },
        { code: "SEARCH_QUOTA_EXHAUSTED", message: "https://internal.example/search" },
      ],
      composition: { mode: "fallback", status: "fallback", latency_ms: 8 },
    };
    const sse = [
      `event: run.started\ndata: ${JSON.stringify({ request_id: requestId, session_id: sessionId })}\n\n`,
      `event: message.completed\ndata: ${JSON.stringify(completed)}\n\n`,
    ].join("");
    await route.fulfill({ status: 200, contentType: "text/event-stream", body: sse });
  });

  await page.goto("/");
  await page.locator("#message-input").fill("这盆绣球多久浇水？");
  await page.locator("#message-input").press("Enter");
  const answer = page.locator(".dynamic-message.assistant-message").last();
  await expect(answer).toContainText("表土干燥后再浇透");
  await expect(answer.locator(".capability-notice")).toHaveCount(2);
  await expect(answer).toContainText("智能回答额度已用完");
  await expect(answer).toContainText("在线资料额度已用完");
  await expect(answer).not.toContainText("internal model vendor");
  await expect(answer).not.toContainText("internal.example");
});
