import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";

test("login, review, evidence, PDF, refresh and mobile layout", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "登录工作台" })).toBeVisible();
  await page.screenshot({ path: "../docs/images/workbench-login.png" });
  await page.getByLabel("邮箱", { exact: true }).fill("demo@example.com");
  await page.getByLabel("密码", { exact: true }).fill("demo-commerce-2026");
  await page.getByRole("button", { name: "登录工作台" }).click();
  await expect(
    page.getByRole("heading", { name: "新建分析", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "../docs/images/workbench-create.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page.getByRole("navigation", { name: "研究记录" }),
  ).not.toBeVisible();
  await page.getByRole("button", { name: "展开分析记录", exact: true }).click();
  await expect(
    page.getByRole("navigation", { name: "研究记录" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "收起分析记录", exact: true }).click();
  await expect(page.getByRole("button", { name: "开始分析" })).toBeDisabled();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.getByRole("heading", { name: "新建分析", exact: true }).click();
  const skipLink = page.getByRole("link", { name: "跳到主要内容" });
  await skipLink.focus();
  await expect(skipLink).toBeInViewport();
  await page.getByRole("heading", { name: "新建分析", exact: true }).click();
  await expect(skipLink).not.toBeInViewport();
  await page.screenshot({
    path: "../docs/images/workbench-create-mobile.png",
    fullPage: true,
  });
  for (const width of [320, 768, 1024]) {
    await page.setViewportSize({ width, height: 1000 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
  }
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.getByLabel("任务类型", { exact: true }).selectOption("research");
  await page
    .getByRole("button", { name: "竞品与库存综合研究", exact: true })
    .click();
  await page.getByLabel("导出前由我复核草稿").check();
  await page.getByRole("button", { name: "开始分析" }).click();
  await expect(page.getByRole("button", { name: "确认并导出" })).toBeVisible({
    timeout: 15000,
  });
  await expect(page.getByLabel("任务类型", { exact: true })).toHaveCount(0);
  await expect(
    page.getByRole("navigation", { name: "报告目录" }),
  ).toBeVisible();
  await expect(
    page.locator(".claim").filter({ hasText: "83766.00" }),
  ).toBeVisible();
  await page.locator(".citations button").first().click();
  await expect(page.locator(".evidence-detail pre")).toContainText("83766.00");
  await page.locator(".ant-drawer-close").click();
  await page.getByRole("button", { name: "确认并导出" }).click();
  const pdf = page.getByRole("link", { name: "report.pdf ↓" });
  await expect(pdf).toBeVisible({ timeout: 15000 });
  const downloadPromise = page.waitForEvent("download");
  await pdf.click();
  const download = await downloadPromise;
  const path = await download.path();
  expect((await readFile(path!)).subarray(0, 4).toString()).toBe("%PDF");
  await page.reload();
  await expect(
    page.getByRole("heading", {
      name: "电商运营分析报告",
      exact: true,
    }),
  ).toBeVisible();
  await expect(page.locator(".event-list li").last()).toContainText(
    "报告已生成",
  );
  await page.screenshot({ path: "../docs/images/workbench-report.png" });
  await page.screenshot({
    path: "../docs/images/commerce-analysis-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("button", { name: "开始分析" })).toHaveCount(0);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  await page
    .getByRole("heading", { name: "电商运营分析报告", exact: true })
    .scrollIntoViewIfNeeded();
  await expect(
    page.locator(".claim").filter({ hasText: "83766.00" }),
  ).toBeVisible();
  await page.screenshot({
    path: "../docs/images/commerce-analysis-mobile.png",
  });
  // Returning to configuration must not create a task or lose the saved report.
  const history = page.getByRole("navigation", { name: "研究记录" });
  await page.getByRole("button", { name: "展开分析记录", exact: true }).click();
  await page.getByLabel("搜索分析记录").fill("没有这个分析记录");
  await expect(history).toContainText("没有匹配的分析记录");
  await page.getByLabel("搜索分析记录").fill("");
  const taskName = await history
    .getByRole("button")
    .first()
    .getAttribute("aria-current");
  expect(taskName).toBe("page");
  await page
    .locator(".sidebar")
    .getByRole("button", { name: "新建分析", exact: true })
    .click();
  await expect(page.getByLabel("任务类型", { exact: true })).toBeVisible();
  await expect(page.locator(".report-paper")).toHaveCount(0);
  await history.getByRole("button").first().click();
  await expect(
    page.getByRole("heading", { name: "电商运营分析报告", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "退出登录", exact: true }).click();
  await expect(page.getByRole("button", { name: "登录工作台" })).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: "../docs/images/workbench-login-mobile.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
});
