import { expect, test } from "@playwright/test";

test("streams a question and keeps the input usable", async ({ page }) => {
  await page.goto("/");
  const input = page.locator("#message-input");
  await input.fill("2025-26 总决赛 G4 谁得分最高？");
  await input.press("Enter");
  await expect(page.locator(".dynamic-message.assistant-message").last()).toContainText("32");
  await expect(page.locator(".dynamic-message.assistant-message").last()).toContainText("演示快照");
  await expect(page.locator("#stream-status")).toBeHidden();
  await expect(input).toBeEnabled();
  await expect(page.locator("#recommendations")).toBeVisible();
  await expect(page.locator(".recommendation-button")).toHaveCount(3);
});

test("keeps a failed public chat request as a retryable error instead of answering from fixtures", async ({ page }) => {
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

  let attempts = 0;
  await page.route("**/api/v1/chat/stream", async (route) => {
    attempts += 1;
    if (attempts === 1) {
      await route.abort("failed");
      return;
    }

    const body = route.request().postDataJSON() as Record<string, unknown>;
    const requestId = "88888888-8888-4888-8888-888888888888";
    const completed = {
      request_id: requestId,
      session_id: String(body.session_id),
      status: "completed",
      answer_markdown: "公开赛事服务已恢复，这次回答来自重新请求。",
      blocks: [{ type: "text", content: "公开赛事服务已恢复，这次回答来自重新请求。" }],
      as_of_beijing: "2026-09-09 09:00",
      evidence_state: "partial",
      data_origin: "public",
      corrections: [],
      follow_up: null,
      latency_ms: 10,
      composition: { mode: "agent", status: "used", latency_ms: 10 },
    };
    const sse = [
      `event: run.started\ndata: ${JSON.stringify({ request_id: requestId, session_id: String(body.session_id) })}\n\n`,
      `event: message.completed\ndata: ${JSON.stringify(completed)}\n\n`,
    ].join("");
    await route.fulfill({ status: 200, contentType: "text/event-stream", body: sse });
  });

  await page.goto("/");
  await expect(page.locator("#connection-label")).toHaveText("API 就绪");
  const input = page.locator("#message-input");
  await input.fill("2025-26 总决赛 G4 谁得分最高？");
  await input.press("Enter");

  const failedAnswer = page.locator(".dynamic-message.assistant-message").last();
  await expect(failedAnswer.locator(".error-card")).toContainText("数据连接暂时中断");
  await expect(failedAnswer.locator(".retry-button")).toBeVisible();
  await expect(failedAnswer).not.toContainText("演示快照");
  await expect(failedAnswer).not.toContainText("杰伦·布朗");
  await expect(failedAnswer).not.toContainText("108–104");

  await failedAnswer.locator(".retry-button").click();
  const recoveredAnswer = page.locator(".dynamic-message.assistant-message").last();
  await expect(recoveredAnswer).toContainText("公开赛事服务已恢复");
  await expect(recoveredAnswer).not.toContainText("演示快照");
  expect(attempts).toBe(2);
});

test("allows transport fallback only when fixture mode was explicitly reported", async ({ page }) => {
  await page.route("**/healthz", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: "ok",
        version: "v1",
        experience: "demo",
        capabilities: { intelligent_analysis: false, default_intelligent_analysis: false },
      }),
    });
  });
  await page.route("**/api/v1/chat/stream", async (route) => route.abort("failed"));

  await page.goto("/");
  await expect(page.locator("#connection-label")).toHaveText("API 就绪");
  const input = page.locator("#message-input");
  await input.fill("2025-26 总决赛 G4 谁得分最高？");
  await input.press("Enter");

  const answer = page.locator(".dynamic-message.assistant-message").last();
  await expect(answer).toContainText("J. Brown");
  await expect(answer).toContainText("演示快照");
});

test("keeps a valid API error visible even in explicit fixture mode", async ({ page }) => {
  await page.route("**/healthz", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: "ok",
        version: "v1",
        experience: "demo",
        capabilities: { intelligent_analysis: false, default_intelligent_analysis: false },
      }),
    });
  });
  await page.route("**/api/v1/chat/stream", async (route) => {
    await route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({
        status: "failed",
        error: { code: "SERVICE_BUSY", retryable: true, message: "服务正在维护，请稍后重试。" },
      }),
    });
  });

  await page.goto("/");
  await expect(page.locator("#connection-label")).toHaveText("API 就绪");
  const input = page.locator("#message-input");
  await input.fill("今天有哪些 NBA 比赛？");
  await input.press("Enter");

  const answer = page.locator(".dynamic-message.assistant-message").last();
  await expect(answer.locator(".error-card")).toContainText("服务正在维护");
  await expect(answer.locator(".retry-button")).toBeVisible();
  await expect(answer).not.toContainText("演示快照");
});

test("shows quota exhaustion notices without discarding a cached answer", async ({ page }) => {
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
  await page.route("**/api/v1/chat/stream", async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    const requestId = "99999999-9999-4999-8999-999999999999";
    const sessionId = String(body.session_id);
    const completed = {
      request_id: requestId,
      session_id: sessionId,
      status: "completed",
      answer_markdown: "已核验记录显示，尼克斯以 94–90 战胜马刺。",
      blocks: [{ type: "text", content: "已核验记录显示，尼克斯以 **94–90** 战胜马刺。" }],
      as_of_beijing: "2026-09-09 21:30",
      evidence_state: "verified",
      data_origin: "public",
      corrections: [],
      notices: [
        {
          code: "INTELLIGENCE_QUOTA_EXHAUSTED",
          message: "智能分析额度已用完，当前回答仅基于已缓存赛事数据。",
          retryable: false,
        },
        {
          code: "SEARCH_QUOTA_EXHAUSTED",
          message: "AcmeSearch API billing quota exhausted at /v1/search.",
          retryable: false,
        },
      ],
      follow_up: null,
      latency_ms: 20,
      composition: { mode: "fallback", status: "fallback", latency_ms: 10 },
    };
    const sse = [
      `event: run.started\ndata: ${JSON.stringify({ request_id: requestId, session_id: sessionId })}\n\n`,
      `event: message.completed\ndata: ${JSON.stringify(completed)}\n\n`,
    ].join("");
    await route.fulfill({ status: 200, contentType: "text/event-stream", body: sse });
  });

  await page.goto("/");
  await page.locator("#message-input").fill("最后一场谁赢了？");
  await page.locator("#message-input").press("Enter");

  const answer = page.locator(".dynamic-message.assistant-message").last();
  await expect(answer).toContainText("94–90");
  await expect(answer.locator(".capability-notice")).toHaveCount(2);
  await expect(answer).toContainText("智能分析额度已用完");
  await expect(answer).toContainText("在线搜索额度已用完");
  await expect(answer).not.toContainText("AcmeSearch");
  await expect(answer).not.toContainText("/v1/search");
  await expect(answer).not.toContainText("暂无数据");
  await expect(page.locator("#message-input")).toBeEnabled();
});

test("shows a non-retryable quota notice when no answer can be produced", async ({ page }) => {
  await page.route("**/api/v1/chat/stream", async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    const requestId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
    const sessionId = String(body.session_id);
    const failed = {
      request_id: requestId,
      session_id: sessionId,
      status: "failed",
      error: {
        code: "COMPOSER_UNAVAILABLE",
        retryable: false,
        message: "智能回答服务当前不可用，暂时无法完成本次回答。",
      },
      notices: [{
        code: "INTELLIGENCE_QUOTA_EXHAUSTED",
        message: "ModelVendor account balance is empty.",
        retryable: false,
      }],
    };
    const sse = [
      `event: run.started\ndata: ${JSON.stringify({ request_id: requestId, session_id: sessionId })}\n\n`,
      `event: run.error\ndata: ${JSON.stringify(failed)}\n\n`,
    ].join("");
    await route.fulfill({ status: 200, contentType: "text/event-stream", body: sse });
  });

  await page.goto("/");
  await page.locator("#message-input").fill("比较两位球员的生涯表现");
  await page.locator("#message-input").press("Enter");

  const answer = page.locator(".dynamic-message.assistant-message").last();
  await expect(answer.locator(".capability-notice")).toHaveCount(1);
  await expect(answer).toContainText("智能分析额度已用完");
  await expect(answer).toContainText("暂时无法完成本次回答");
  await expect(answer).not.toContainText("ModelVendor");
  await expect(answer.locator(".retry-button")).toHaveCount(0);
  await expect(page.locator("#message-input")).toBeEnabled();
});

test("keeps next questions on the verified roaming matchup without guessing a winner", async ({ page }) => {
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
  let turn = 0;
  await page.route("**/api/v1/chat/stream", async (route) => {
    turn += 1;
    const body = route.request().postDataJSON() as Record<string, unknown>;
    const requestId = `77777777-7777-4777-8777-${String(turn).padStart(12, "0")}`;
    const sessionId = String(body.session_id);
    const answers = [
      {
        answer: "尼克斯 4–1 击败马刺，赢下这轮系列赛。系列赛共 5 场。",
        evidence: "verified",
        blocks: [
          { type: "text", content: "尼克斯 **4–1** 击败马刺，赢下这轮系列赛。" },
          {
            type: "table",
            label: "系列赛赛果",
            columns: ["场次", "客队", "比分", "主队"],
            rows: [
              ["G4", "马刺", "106–107", "尼克斯"],
              ["G5", "尼克斯", "94–90", "马刺"],
            ],
          },
        ],
      },
      {
        answer: "如果按戏剧性和系列赛转折，我会选 G4：尼克斯 107–106 马刺。",
        evidence: "partial",
        blocks: [],
      },
      {
        answer: "你好，可以问我 NBA 赛程、球员或球队。",
        evidence: "none",
        blocks: [],
      },
    ];
    const selected = answers[Math.min(turn - 1, answers.length - 1)];
    const completed = {
      request_id: requestId,
      session_id: sessionId,
      status: "completed",
      answer_markdown: selected.answer,
      blocks: selected.blocks,
      as_of_beijing: selected.evidence === "none" ? null : "2026-09-09 08:40",
      evidence_state: selected.evidence,
      data_origin: selected.evidence === "none" ? "none" : "public",
      corrections: [],
      // A stale server follow-up must not displace locally grounded prompts.
      follow_up: "今天有哪些 NBA 比赛？",
      latency_ms: 10,
      composition: { mode: "agent", status: "used", latency_ms: 10 },
    };
    const sse = [
      `event: run.started\ndata: ${JSON.stringify({ request_id: requestId, session_id: sessionId })}\n\n`,
      `event: message.completed\ndata: ${JSON.stringify(completed)}\n\n`,
    ].join("");
    await route.fulfill({ status: 200, contentType: "text/event-stream", body: sse });
  });

  await page.goto("/");
  const input = page.locator("#message-input");
  await input.fill("2026尼克斯-马刺");
  await input.press("Enter");
  const recommendations = page.locator("#recommendation-list");
  await expect(recommendations).toContainText("尼克斯 与 马刺 这轮哪场最值得回看？");
  await expect(recommendations).toContainText("这轮系列赛的收官战是怎么赢下来的？");
  await expect(recommendations).toContainText("比较这轮系列赛的 G2 和 G4。");
  await expect(recommendations).not.toContainText("今天有哪些 NBA 比赛？");
  await expect(recommendations).not.toContainText("尼克斯 为什么能赢");
  await expect(recommendations).not.toContainText("马刺 为什么能赢");

  await input.fill("你觉得最精华的是哪一场");
  await input.press("Enter");
  await expect(page.locator(".dynamic-message.assistant-message").last()).toContainText("G4");
  await expect(recommendations).toContainText("尼克斯 与 马刺 这轮哪场最值得回看？");
  await expect(recommendations).not.toContainText("今天有哪些 NBA 比赛？");

  await page.locator("#new-session").click();
  await input.fill("你好");
  await input.press("Enter");
  await expect(recommendations).toContainText("今天有哪些 NBA 比赛？");
  await expect(recommendations).toContainText("最近一场比赛的关键回合是什么？");
  await expect(recommendations).not.toContainText("尼克斯");
  await expect(recommendations).not.toContainText("马刺");
});

test("hides supplemental search evidence from the user answer", async ({ page }) => {
  await page.route("**/api/v1/chat/stream", async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    const requestId = "66666666-6666-4666-8666-666666666666";
    const sessionId = String(body.session_id);
    const answer = [
      "结构化比赛记录暂未找到直接匹配。",
      "缓存命中后，系统复用了上一轮结果。",
      "我查询了结构化比赛记录，但没有找到直接匹配。",
      "本轮已调用工具完成搜索。",
      "我们搜索后发现，尼克斯守住了领先。",
      "命中缓存后，系统复用了数据库结果。",
      "我查询了结构化记录，但没有找到逐回合。",
      "已调用工具完成搜索。",
      "Hermes provider 返回了缓存中的搜索摘要。",
      "结论：尼克斯赢球，补充线索：新闻标题和摘要。",
      "",
      "**搜索摘要**",
      "未加项目符号的新闻标题",
      "未加项目符号的新闻摘要。",
      "因此，搜索摘要仍不应被当作最终回答。",
      "- **第一条报道**：报道摘要。",
      "公开资料摘要：",
      "- **第二条报道**：另一条摘要。",
      "- **第三条报道**：第三条摘要。",
      "",
      "以上公开资料仅作线索，尚未交叉核验。",
      "综合结论：尼克斯在收官阶段守住了优势。",
    ].join("\n");
    const completed = {
      request_id: requestId,
      session_id: sessionId,
      status: "completed",
      answer_markdown: answer,
      blocks: [
        { type: "text", content: answer },
        { type: "fact", label: "缓存命中", value: "系统复用了数据库结果" },
        { type: "fact", label: "运行说明", value: "已调用工具完成搜索。" },
        {
          type: "table",
          label: "结构化比赛记录查询结果",
          columns: ["工具", "状态"],
          rows: [["已调用工具", "完成搜索"]],
        },
        {
          type: "table",
          label: "执行明细",
          columns: ["类别", "内容"],
          rows: [
            ["运行时", "Hermes provider"],
            ["存储", "命中缓存后返回数据库结果。"],
          ],
        },
      ],
      as_of_beijing: "2026-09-03 15:30",
      evidence_state: "partial",
      data_origin: "public",
      corrections: [],
      follow_up: null,
      latency_ms: 10,
      composition: { mode: "agent", status: "used", latency_ms: 10 },
    };
    const sse = [
      `event: run.started\ndata: ${JSON.stringify({ request_id: requestId, session_id: sessionId })}\n\n`,
      `event: message.completed\ndata: ${JSON.stringify(completed)}\n\n`,
    ].join("");
    await route.fulfill({ status: 200, contentType: "text/event-stream", body: sse });
  });

  await page.goto("/");
  await page.locator("#message-input").fill("2026尼克斯-马刺");
  await page.locator("#message-input").press("Enter");
  const answer = page.locator(".dynamic-message.assistant-message").last();
  await expect(answer.locator(".markdown-list li")).toHaveCount(0);
  await expect(answer).not.toContainText("补充线索");
  await expect(answer).not.toContainText("公开资料线索");
  await expect(answer).not.toContainText("搜索摘要");
  await expect(answer).not.toContainText("公开资料摘要");
  await expect(answer).not.toContainText("公开报道尚未与结构化比赛记录交叉核验");
  await expect(answer).not.toContainText("这些内容不标记为已核验事实");
  await expect(answer).not.toContainText("第一条报道");
  await expect(answer).not.toContainText("未加项目符号的新闻标题");
  await expect(answer).not.toContainText("未加项目符号的新闻摘要");
  await expect(answer).not.toContainText("因此，搜索摘要仍不应被当作最终回答");
  await expect(answer).not.toContainText("结构化比赛记录暂未找到直接匹配");
  await expect(answer).not.toContainText("缓存命中");
  await expect(answer).not.toContainText("我查询了结构化比赛记录");
  await expect(answer).not.toContainText("已调用工具");
  await expect(answer).not.toContainText("系统复用了数据库结果");
  await expect(answer).not.toContainText("结构化比赛记录查询结果");
  await expect(answer).not.toContainText("我们搜索后发现");
  await expect(answer).not.toContainText("命中缓存后");
  await expect(answer).not.toContainText("我查询了结构化记录");
  await expect(answer).not.toContainText("Hermes");
  await expect(answer).not.toContainText("provider");
  await expect(answer).not.toContainText("运行说明");
  await expect(answer).not.toContainText("执行明细");
  await expect(answer).not.toContainText("新闻标题和摘要");
  await expect(answer).toContainText("目前缺少逐回合");
  await expect(answer).toContainText("结论：尼克斯赢球");
  await expect(answer).toContainText("综合结论：尼克斯在收官阶段守住了优势");
});

test("buffers split internal headings while an answer is streaming", async ({ page }) => {
  await page.goto("/");

  const result = await page.evaluate(() => {
    const project = window.CourtsideMarkdownUtils.projectStreamingAnswer;
    const strip = window.CourtsideMarkdownUtils.stripSupplementalEvidence;
    return {
      projections: [
        project("搜"),
        project("搜索摘"),
        project("结论：尼克斯赢球。补充线"),
        project("根据搜索结果，尼克斯守住了领先。"),
        project("我们搜"),
        project("命中缓"),
        project("Herm"),
      ],
      cleaned: strip([
        "已经拿到比分硬事实和过程线索，综合回答。",
        "结论：尼克斯 94–90 赢球。",
        "需要说明的是，结构化记录没有逐节攻防走势，打法细节（哪些回合拉开、何时反超）来自公开报道线索，措辞以分析限定；比分和胜者这些是已核验的硬事实。",
      ].join("\n")),
      awards: [
        "他本场当选总决赛 MVP",
        "本场布伦森当选总决赛 MVP",
        "布伦森在这场比赛荣膺总决赛最有价值球员",
        "G5 布伦森获得 FMVP",
      ].map((value) => strip(value)),
      productionMeta: strip([
        "搜索结果存在不一致，来源间分数说法不同，且包含未经核验的信息。我只使用能确认的部分，谨慎综合。",
        "先给结论，再给有限的分析，并明确标注哪些是确认事实、哪些过程细节尚不足以确认。",
        "**结论：尼克斯赢了。** 尼克斯 94–90 击败马刺。",
        "可以确认的硬事实：",
        "- 布伦森得到 45 分。",
        "目前缺少逐回合数据，公开报道的说法也不完全一致（分数口径不一致），过程细节尚不足以确认，我不能凭空替你还原每节攻防。",
        "可以推断的有限可能因素：尼克斯的防守可能起了作用。",
      ].join("\n")),
    };
  });

  expect(result.projections).toEqual([
    "",
    "",
    "结论：尼克斯赢球。",
    "尼克斯守住了领先。",
    "",
    "",
    "",
  ]);
  expect(result.cleaned).toContain("结论：尼克斯 94–90 赢球。");
  expect(result.cleaned).toContain("目前缺少逐节攻防走势");
  expect(result.cleaned).toContain("无法可靠还原具体回合和反超节点");
  expect(result.cleaned).not.toContain("结构化记录");
  expect(result.cleaned).not.toContain("硬事实");
  result.awards.forEach((value) => {
    expect(value).toContain("最终当选总决赛 MVP");
    expect(value).not.toContain("本场当选");
    expect(value).not.toContain("这场比赛荣膺");
    expect(value).not.toContain("G5 布伦森获得");
  });
  expect(result.productionMeta).toContain("**结论：尼克斯赢了。**");
  expect(result.productionMeta).toContain("**关键数据**");
  expect(result.productionMeta).toContain("**谨慎分析**");
  expect(result.productionMeta).not.toMatch(/搜索结果|来源间|未经核验|我只使用|先给结论|明确标注|硬事实|公开报道|分数口径|凭空/u);
});

test("roaming questions do not implicitly select the first replay card", async ({ page }) => {
  let selectedGameId: unknown = "not-set";
  await page.route("**/api/v1/chat/stream", async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    selectedGameId = body.selected_game_id;
    const requestId = "55555555-5555-4555-8555-555555555555";
    const sessionId = String(body.session_id);
    const answer = "今天有哪些 NBA 比赛？";
    const completed = {
      request_id: requestId,
      session_id: sessionId,
      status: "needs_clarification",
      answer_markdown: "请指定日期，我再帮您查询。",
      blocks: [{ type: "text", content: "请指定日期，我再帮您查询。" }],
      as_of_beijing: null,
      evidence_state: "none",
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
  await expect(page.locator("#conversation-scope")).toHaveText("漫游模式");
  await expect(page.locator("#game-list .game-list-card")).toHaveCount(0);
  await page.locator("#message-input").fill("今天有哪些 NBA 比赛？");
  await page.locator("#message-input").press("Enter");
  await expect(page.locator(".dynamic-message.assistant-message").last()).toContainText("请指定日期");
  expect(selectedGameId).toBeUndefined();
  await expect(page.locator("#conversation-scope")).toHaveText("漫游模式");
});

test("answers venue metadata from the selected replay without unrelated scores", async ({ page }) => {
  await page.goto("/");
  await page.locator('[data-highlight-mode="history"]').click();
  await expect(page.locator("#game-list .game-list-card")).toHaveCount(5);
  await page.locator("#game-list .game-list-card").first().click();
  await expect(page.locator("#conversation-scope")).toHaveText("赛事下钻");
  const input = page.locator("#message-input");
  await input.fill("这场比赛在哪儿进行的？");
  await input.press("Enter");
  const answer = page.locator(".dynamic-message.assistant-message").last();
  await expect(answer).toContainText("TD Garden");
  await expect(answer).toContainText("Boston");
  await expect(answer).not.toContainText("108–104");
  await expect(answer).toContainText("固定演示快照");
});

test("recalls an indexed prior question from the same bounded session", async ({ page }) => {
  await page.goto("/");
  const input = page.locator("#message-input");
  for (const question of [
    "你好",
    "你是谁",
    "2025-26 总决赛 G4 最后 5 秒发生了什么？",
  ]) {
    await input.fill(question);
    await input.press("Enter");
    await expect(page.locator("#stream-status")).toBeHidden();
  }
  await input.fill("我第三个问题问的啥");
  await input.press("Enter");
  const answer = page.locator(".dynamic-message.assistant-message").last();
  await expect(answer).toContainText("第 3 个问题");
  await expect(answer).toContainText("最后 5 秒");
  await expect(answer).toContainText("会话处理");
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

test("keeps accurate session metadata across refresh and resets on new chat", async ({ page }) => {
  await page.goto("/");
  const input = page.locator("#message-input");
  await input.fill("你好");
  await input.press("Enter");
  await expect(page.locator(".dynamic-message.assistant-message").last()).toContainText("COURTSIDE");

  const originalSessionId = await page.evaluate(
    () => window.sessionStorage.getItem("courtside-demo-session-v1"),
  );
  await page.reload();
  await page.locator("#message-input").fill("我问了你几个问题？");
  await page.locator("#message-input").press("Enter");
  await expect(page.locator(".dynamic-message.assistant-message").last()).toContainText(
    "此前问了 1 个问题",
  );
  await expect(page.locator(".dynamic-message.assistant-message").last()).toContainText("已回答");
  await expect(page.locator(".dynamic-message.assistant-message").last()).toContainText("会话处理");
  await expect(page.locator(".dynamic-message.assistant-message").last()).not.toContainText("暂无数据");

  await page.locator("#new-session").click();
  const freshSessionId = await page.evaluate(
    () => window.sessionStorage.getItem("courtside-demo-session-v1"),
  );
  expect(freshSessionId).not.toBe(originalSessionId);
  await page.locator("#message-input").fill("我问了你几个问题？");
  await page.locator("#message-input").press("Enter");
  await expect(page.locator(".dynamic-message.assistant-message").last()).toContainText(
    "此前问了 0 个问题",
  );
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
  await page.locator('[data-highlight-mode="history"]').click();
  await expect(page.locator("#game-list .game-list-card")).toHaveCount(5);
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
  await page.locator('[data-highlight-mode="history"]').click();
  await expect(page.locator("#game-list .game-list-card")).toHaveCount(5);
  await page.locator("#game-list .game-list-card").first().click();
  await page.locator("#message-input").fill("这场比赛在哪儿举办的？");
  await page.locator("#message-input").press("Enter");
  await expect(page.locator(".dynamic-message.assistant-message").last()).toContainText("雷霆");
  await expect(page.locator(".dynamic-message.assistant-message").last()).toContainText("凯尔特人");
  expect(selectedGameId).toBe("2026-finals-g4");
});

test("explicit game wording is parsed without changing the clicked drill-down card", async ({ page }) => {
  const selectedGameIds: unknown[] = [];
  await page.route("**/api/v1/chat/stream", async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    selectedGameIds.push(body.selected_game_id);
    const requestId = `33333333-3333-4333-8333-${String(selectedGameIds.length).padStart(12, "0")}`;
    const sessionId = String(body.session_id);
    const answer = String(body.message).includes("G4")
      ? "杰伦·布朗得到 32 分。"
      : "这场比赛仍是勇士对掘金。";
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
  await page.locator('[data-highlight-mode="history"]').click();
  await expect(page.locator("#game-list .game-list-card")).toHaveCount(5);
  // Pick the second card first, then explicitly ask about G4.  Textual game
  // wording is a query condition, not a UI selection; the clicked card stays
  // authoritative for the later pronoun turn.
  await page.locator("#game-list .game-list-card").nth(1).click();
  const input = page.locator("#message-input");
  await input.fill("2025-26 总决赛 G4 谁得分最高？");
  await input.press("Enter");
  await expect(page.locator(".dynamic-message.assistant-message").last()).toContainText("32");
  await input.fill("这场比赛谁打谁");
  await input.press("Enter");
  await expect(page.locator(".dynamic-message.assistant-message").last()).toContainText("勇士对掘金");
  expect(selectedGameIds).toEqual(["2026-demo-den-gsw", "2026-demo-den-gsw"]);
  await expect(page.locator("#conversation-scope")).toHaveText("赛事下钻");
});

test("full intelligence acceptance prompts render Agent provenance", async ({ page }) => {
  const requestBodies: Array<Record<string, unknown>> = [];
  await page.route("**/healthz", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        status: "ok",
        version: "v1",
        experience: "public",
        capabilities: { intelligent_analysis: true, default_intelligent_analysis: true },
        dependencies: { session_store: "ok", cache: "ok", assistant_runtime: "ok", auth: "ok", web_search: "enabled" },
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
    await expect(page.locator(".composition-chip.agent").last()).not.toContainText("已调用工具");
    await expect(page.locator("body")).not.toContainText("Hermes");
    await expect(page.locator("body")).not.toContainText("NBA AGENT");
    await expect(page.locator("body")).not.toContainText("Agent API");
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
  // Reload preserves the chat session, but the highlights projection is
  // browse-only until the user explicitly enters a card.
  await expect(page.locator("#conversation-scope")).toHaveText("漫游模式");
  await expect(page.locator("#game-list .game-list-card").first()).toHaveAttribute("aria-pressed", "false");
  await page.locator("#new-session").click();
  const newSessionId = await page.evaluate(() => window.sessionStorage.getItem("courtside-demo-session-v1"));
  expect(newSessionId).not.toBe(logicalSessionId);
  await expect(page.locator("#game-list .game-list-card").first()).toHaveAttribute("aria-pressed", "false");
});
