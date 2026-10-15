import { expect, test, type Page } from "@playwright/test";
import type { Task, TaskEvent } from "../src/types";

// Sanitized contract fixture based on the real 120.477s planning timeout.
// Browser responses are mocked; these tests do not call or evaluate an LLM.
const failed: Task = {
  id: "4b007633-3eb9-4cf5-a57f-8c396605f787",
  conversation_id: "diagnostic-fixture",
  query: "检查综合补货失败原因",
  status: "failed",
  mode: "live",
  created_at: "2026-10-15T02:59:43Z",
  error_code: "APITimeoutError",
  parameters: {
    start_date: "2026-09-01",
    end_date: "2026-09-30",
    strategy: "single",
  },
  report: null,
  validation: null,
  metrics: {
    tool_calls: 0,
    model_calls: 1,
    runtime_ms: 120477,
    input_tokens: 0,
    output_tokens: 0,
    usage_complete: false,
  },
};
const modelError: TaskEvent = {
  id: 4,
  kind: "model_error",
  message: "模型请求失败，Token使用量未知",
  timestamp: "2026-10-15T03:01:44Z",
  data: {
    error_type: "APITimeoutError",
    category: "timeout",
    timeout_phase: "read",
    elapsed_ms: 120332,
    raw_provider_message: "secret-never-render-this",
  },
};

async function openFixture(page: Page, task: Task, events: TaskEvent[]) {
  const writes: string[] = [];
  const browserErrors: string[] = [];
  page.on("pageerror", (error) => browserErrors.push(error.message));
  await page.route("**/api/tasks**", async (route) => {
    if (route.request().method() !== "GET") {
      writes.push(route.request().method());
      await route.abort();
      return;
    }
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path === "/api/tasks") await route.fulfill({ json: { tasks: [task] } });
    else if (path.endsWith("/events")) {
      const after = Number(url.searchParams.get("after") || 0);
      const selected = events.filter((event) => event.id > after);
      await route.fulfill({
        json: { events: selected, cursor: selected.at(-1)?.id || after },
      });
    } else if (path.endsWith("/evidence"))
      await route.fulfill({ json: { evidence: [] } });
    else await route.fulfill({ json: task });
  });
  await page.goto("/");
  await page.getByLabel("邮箱", { exact: true }).fill("demo@example.com");
  await page.getByLabel("密码", { exact: true }).fill("demo-commerce-2026");
  await page.getByRole("button", { name: "登录工作台" }).click();
  await page.getByRole("button", { name: task.query }).click();
  return { writes, browserErrors };
}

test("read timeout survives refresh, shows unknown usage, and never retries", async ({
  page,
}, testInfo) => {
  const observed = await openFixture(page, failed, [modelError]);
  const diagnostic = page.getByLabel("任务诊断");
  await expect(diagnostic).toContainText("模型请求超时");
  await expect(diagnostic).toContainText("超时阶段：等待响应数据");
  await expect(diagnostic).toContainText("本次模型请求耗时约 120.3 秒");
  await expect(diagnostic).toContainText("模型用量不完整，费用尚不能确认");
  await expect(diagnostic).not.toContainText("secret-never-render-this");
  await diagnostic.getByText("排查信息", { exact: true }).click();
  await expect(diagnostic).toContainText(failed.id);
  await expect(diagnostic).toContainText("APITimeoutError");
  await diagnostic.screenshot({
    path: testInfo.outputPath("diagnostic-desktop.png"),
  });
  await page.reload();
  await expect(diagnostic).toContainText("超时阶段：等待响应数据");
  await page.setViewportSize({ width: 390, height: 844 });
  await diagnostic.scrollIntoViewIfNeeded();
  await diagnostic.getByText("排查信息", { exact: true }).click();
  await expect(diagnostic).toContainText(failed.id);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  await diagnostic.screenshot({
    path: testInfo.outputPath("diagnostic-mobile.png"),
  });
  expect(observed.writes).toEqual([]);
  expect(observed.browserErrors).toEqual([]);
});

for (const [code, category, title] of [
  ["AuthenticationError", "authentication", "模型服务认证失败"],
  ["RateLimitError", "rate_limit", "模型服务请求受限"],
  ["APIConnectionError", "connection", "无法完成与模型服务的连接"],
]) {
  test(`provider category ${category} has an actionable message`, async ({
    page,
  }) => {
    const observed = await openFixture(page, { ...failed, error_code: code }, [
      {
        ...modelError,
        data: {
          error_type: code,
          category,
          raw_provider_message: "secret-never-render-this",
        },
      },
    ]);
    const diagnostic = page.getByLabel("任务诊断");
    await expect(diagnostic).toContainText(title);
    await expect(diagnostic).not.toContainText("超时阶段");
    await expect(diagnostic).not.toContainText("secret-never-render-this");
    expect(observed.writes).toEqual([]);
    expect(observed.browserErrors).toEqual([]);
  });
}

test("terminal runtime limit takes precedence over earlier model timeout", async ({
  page,
}) => {
  const observed = await openFixture(
    page,
    { ...failed, error_code: "runtime_budget" },
    [modelError],
  );
  const diagnostic = page.getByLabel("任务诊断");
  await expect(diagnostic).toContainText("任务达到执行总时限");
  await expect(diagnostic).not.toContainText("超时阶段");
  await expect(diagnostic).toContainText("模型用量不完整");
  expect(observed.writes).toEqual([]);
});

test("legacy and unknown diagnostic fields cannot invent a timeout phase", async ({
  page,
}) => {
  const observed = await openFixture(page, failed, [
    {
      ...modelError,
      data: {
        error_type: "APITimeoutError",
        category: "__proto__",
        timeout_phase: "constructor",
        elapsed_ms: "secret-never-render-this",
        response_body: "secret-never-render-this",
      },
    },
  ]);
  const diagnostic = page.getByLabel("任务诊断");
  await expect(diagnostic).toContainText("模型请求超时");
  await expect(diagnostic).toContainText("超时阶段：尚无法确认");
  await expect(diagnostic).not.toContainText("本次模型请求耗时");
  await expect(diagnostic).not.toContainText("secret-never-render-this");
  expect(observed.browserErrors).toEqual([]);
});

test("research fallback is visible while its report remains readable", async ({
  page,
}) => {
  const task: Task = {
    ...failed,
    status: "degraded",
    error_code: null,
    report: {
      title: "教学报告",
      sections: [
        {
          name: "行动建议",
          claims: [
            {
              id: "excerpt",
              kind: "fact",
              text: "教学规则原文摘录：建议需人工确认。",
              evidence_ids: [],
            },
          ],
        },
      ],
      limitations: ["研究语义复核未完成，仅保留原文摘录"],
    },
    validation: {
      passed: true,
      semantic_status: "model_reviewed",
      note: "重要结论仍须人工核验",
      issues: [],
      research_validation: { passed: false, semantic_status: "unavailable" },
    },
    metrics: {
      ...failed.metrics,
      input_tokens: 100,
      output_tokens: 50,
      usage_complete: true,
    },
  };
  const observed = await openFixture(page, task, []);
  const diagnostic = page.getByLabel("任务诊断");
  await expect(diagnostic).toContainText("研究结论未通过完整复核");
  await expect(diagnostic).not.toContainText("任务未能完成");
  await expect(diagnostic).not.toContainText("模型用量不完整");
  await expect(page.locator(".claim")).toContainText("教学规则原文摘录");
  await page.reload();
  await expect(diagnostic).toContainText("研究结论未通过完整复核");
  expect(observed.writes).toEqual([]);
});
