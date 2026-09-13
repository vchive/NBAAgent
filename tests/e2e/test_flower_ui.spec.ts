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

test("keeps composed prose after a raw search block without exposing snippets", async ({ page }) => {
  await page.goto("/");
  const result = await page.evaluate(() => {
    const utils = window.CourtsideMarkdownUtils;
    return {
      withoutBoundary: utils.stripSupplementalEvidence([
        "补充线索：",
        "- 一条网页标题：https://example.invalid/a",
        "- 另一条摘要",
        "",
        "尼克斯的收官策略应先看可确认的比分和现场观察。",
      ].join("\n")),
      ordinaryLineAfterHeading: utils.stripSupplementalEvidence([
        "搜索结果：",
        "这是一段已经整理好的自然语言回答，没有原始列表。",
      ].join("\n")),
      streamingLeak: utils.projectStreamingAnswer(
        "绣球的表土干燥后再浇透。H e-r-m-e-s provider https://private.invalid/x",
      ),
    };
  });

  expect(result.withoutBoundary).toContain("尼克斯的收官策略");
  expect(result.withoutBoundary).not.toContain("网页标题");
  expect(result.withoutBoundary).not.toContain("example.invalid");
  expect(result.ordinaryLineAfterHeading).toContain("已经整理好的自然语言回答");
  expect(result.streamingLeak).toContain("绣球的表土干燥后再浇透");
  expect(result.streamingLeak).not.toMatch(/Hermes|provider|private\.invalid/iu);
});

test("does not expose request ids or unsafe follow-up/provenance text", async ({ page }) => {
  await page.route("**/api/v1/chat/stream", async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    const requestId = "deadbeef-dead-4ead-8ead-deadbeef0001";
    const sessionId = String(body.session_id || "");
    const completed = {
      request_id: requestId,
      session_id: sessionId,
      status: "completed",
      answer_markdown: "月季先检查盆土干湿。",
      blocks: [{ type: "text", content: "月季先检查盆土干湿。" }],
      evidence_state: "partial",
      data_origin: "local_knowledge",
      as_of_beijing: "provider endpoint https://private.invalid",
      follow_up: "继续查看 https://private.invalid/search（provider）",
      notices: [{ code: "UNKNOWN_INTERNAL_CODE", message: "provider secret" }],
      composition: { mode: "model", status: "used", latency_ms: 8 },
    };
    const sse = [
      `event: run.started\ndata: ${JSON.stringify({ request_id: requestId, session_id: sessionId })}\n\n`,
      `event: message.completed\ndata: ${JSON.stringify(completed)}\n\n`,
    ].join("");
    await route.fulfill({ status: 200, contentType: "text/event-stream", body: sse });
  });

  await page.goto("/");
  await page.locator("#message-input").fill("月季怎么养？");
  await page.locator("#message-input").press("Enter");
  const answer = page.locator(".dynamic-message.assistant-message").last();
  await expect(answer).toContainText("月季先检查盆土干湿");
  await expect(answer.locator(".request-reference")).toHaveCount(0);
  await expect(answer).not.toContainText("deadbeef");
  await expect(answer).not.toContainText("private.invalid");
  await expect(answer).not.toContainText("provider");
  await expect(answer.locator(".follow-up-button")).toHaveCount(0);
});

test("maps failed capability codes to stable public copy", async ({ page }) => {
  await page.route("**/api/v1/chat/stream", async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    const requestId = "feedface-feed-4eed-8eed-feedface0002";
    const sessionId = String(body.session_id || "");
    const failed = {
      request_id: requestId,
      session_id: sessionId,
      status: "failed",
      error: {
        code: "SEARCH_QUOTA_EXHAUSTED",
        retryable: true,
        message: "Vendor billing quota at https://private.invalid/search",
      },
    };
    const sse = [
      `event: run.started\ndata: ${JSON.stringify({ request_id: requestId, session_id: sessionId })}\n\n`,
      `event: run.error\ndata: ${JSON.stringify(failed)}\n\n`,
    ].join("");
    await route.fulfill({ status: 200, contentType: "text/event-stream", body: sse });
  });

  await page.goto("/");
  await page.locator("#message-input").fill("查一下上海近期花况");
  await page.locator("#message-input").press("Enter");
  const answer = page.locator(".dynamic-message.assistant-message").last();
  await expect(answer.locator(".error-card")).toContainText("在线资料额度已用完");
  await expect(answer).not.toContainText("Vendor");
  await expect(answer).not.toContainText("private.invalid");
  await expect(answer).not.toContainText("feedface");
});
