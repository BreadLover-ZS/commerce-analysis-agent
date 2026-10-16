import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";

test("login, review, evidence, PDF, refresh and mobile layout", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await page.getByLabel("邮箱", { exact: true }).fill("demo@example.com");
  await page.getByLabel("密码", { exact: true }).fill("demo-commerce-2026");
  await page.getByRole("button", { name: "登录工作台" }).click();
  await expect(
    page.getByRole("heading", { name: "从数据到有依据的建议" }),
  ).toBeVisible();
  await page.getByLabel("任务类型", { exact: true }).selectOption("research");
  await page
    .getByRole("button", { name: "竞品与库存综合研究", exact: true })
    .click();
  await page.getByLabel("导出前由我复核草稿").check();
  await page.getByRole("button", { name: "开始分析" }).click();
  await expect(page.getByRole("button", { name: "确认并导出" })).toBeVisible({
    timeout: 15000,
  });
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
  await page.screenshot({
    path: "../docs/images/commerce-analysis-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("button", { name: "开始分析" })).toBeVisible();
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
  expect(errors).toEqual([]);
});
