import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";

test("scenario preview, version binding, review, evidence and PDF", async ({
  page,
}, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await page.getByLabel("邮箱", { exact: true }).fill("demo@example.com");
  await page.getByLabel("密码", { exact: true }).fill("demo-commerce-2026");
  await page.getByRole("button", { name: "登录工作台" }).click();
  await expect(page.getByLabel("任务类型", { exact: true })).toHaveValue(
    "commerce_review",
  );
  await expect(page.getByRole("button", { name: "开始分析" })).toBeDisabled();
  await page
    .getByLabel("模拟场景", { exact: true })
    .selectOption("top_decline");
  await page.getByRole("button", { name: "生成模拟数据" }).click();
  await expect(page.getByLabel("导入预览", { exact: true })).toContainText(
    "模拟数据",
  );
  await page.getByRole("button", { name: "确认使用此数据版本" }).click();
  await expect(page.getByLabel("数据质量检查", { exact: true })).toContainText(
    "缺失0条",
  );
  await expect(page.getByRole("button", { name: "开始分析" })).toBeEnabled();
  // Reconfirming the same content is idempotent and must not leave validation disabled.
  await page.getByRole("button", { name: "生成模拟数据" }).click();
  await page.getByRole("button", { name: "确认使用此数据版本" }).click();
  await expect(page.getByRole("button", { name: "开始分析" })).toBeEnabled();
  await page.getByLabel("导出前由我复核草稿").check();
  const submission = page.waitForRequest(
    (request) =>
      new URL(request.url()).pathname === "/api/tasks" &&
      request.method() === "POST",
  );
  await page.getByRole("button", { name: "开始分析" }).click();
  const params = (await submission).postDataJSON();
  expect(params.profile).toBe("commerce_review");
  expect(params.sources).toEqual(["sales"]);
  expect(params.strategy).toBe("workflow");
  expect(params.dataset_id).toBeTruthy();
  await expect(page.getByRole("button", { name: "确认并导出" })).toBeVisible({
    timeout: 15000,
  });
  await expect(
    page.getByRole("heading", { name: "经营复盘报告", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "变化贡献", exact: true }),
  ).toBeVisible();
  // Dataset selection survives visiting a report and coming back to configuration.
  const record = page
    .getByRole("navigation", { name: "研究记录" })
    .getByRole("button")
    .first();
  await page
    .locator(".sidebar")
    .getByRole("button", { name: "新建分析", exact: true })
    .click();
  await expect(
    page.getByLabel("已确认的数据版本", { exact: true }),
  ).toHaveValue(params.dataset_id);
  await expect(page.getByRole("button", { name: "开始分析" })).toBeEnabled();
  await record.click();
  await expect(
    page.getByRole("heading", { name: "经营复盘报告", exact: true }),
  ).toBeVisible();
  await page.locator(".citations button").first().click();
  await expect(page.locator(".evidence-detail pre")).toContainText(
    params.dataset_id,
  );
  await expect(page.locator(".evidence-detail pre")).toContainText(
    '"sample": true',
  );
  await page.locator(".ant-drawer-close").click();
  await page.getByRole("button", { name: "确认并导出" }).click();
  const pdf = page.getByRole("link", { name: "report.pdf ↓" });
  await expect(pdf).toBeVisible({ timeout: 15000 });
  const downloading = page.waitForEvent("download");
  await pdf.click();
  const download = await downloading;
  expect(
    (await readFile((await download.path())!)).subarray(0, 4).toString(),
  ).toBe("%PDF");
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "经营复盘报告", exact: true }),
  ).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: testInfo.outputPath("review-mobile.png"),
    fullPage: true,
  });
  expect(errors).toEqual([]);
});

test("invalid CSV shows row errors and missing data remains explicit", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByLabel("邮箱", { exact: true }).fill("demo@example.com");
  await page.getByLabel("密码", { exact: true }).fill("demo-commerce-2026");
  await page.getByRole("button", { name: "登录工作台" }).click();
  await page.getByLabel("导入经营CSV", { exact: true }).setInputFiles({
    name: "invalid.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(
      "sku,name,category,active_from,day,paid_units,refunded_units,revenue_cents,refund_cents\nA,商品,日用,2026-09-01,2026-09-01,-1,0,100,0\n",
    ),
  });
  await expect(page.getByLabel("经营数据管理", { exact: true })).toContainText(
    "第2行",
  );
  await expect(
    page.getByRole("button", { name: "确认使用此数据版本" }),
  ).toHaveCount(0);
  await page.getByLabel("模拟场景", { exact: true }).selectOption("missing");
  await page.getByRole("button", { name: "生成模拟数据" }).click();
  await expect(page.getByLabel("导入预览", { exact: true })).toContainText(
    "缺失未补零",
  );
  await page.getByRole("button", { name: "确认使用此数据版本" }).click();
  await expect(page.getByLabel("数据质量检查", { exact: true })).toContainText(
    "缺失1条",
  );
  await page.getByLabel("开始日期", { exact: true }).fill("2026-01-01");
  await expect(page.getByRole("button", { name: "开始分析" })).toBeDisabled();
  await expect(page.getByLabel("经营数据管理", { exact: true })).toContainText(
    "周期为1–90天",
  );
});
