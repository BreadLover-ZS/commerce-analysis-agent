export const activeStatuses = new Set([
  "queued",
  "running",
  "cancelling",
  "waiting_review",
  "waiting_input",
]);

export const statusLabels: Record<string, string> = {
  queued: "排队中",
  running: "分析中",
  cancelling: "取消中",
  waiting_review: "待复核",
  waiting_input: "待澄清",
  expired: "等待已过期",
  succeeded: "已完成",
  degraded: "已完成 · 有信息缺口",
  failed: "执行失败",
  cancelled: "已取消",
  rejected: "已驳回",
};

export function statusColor(status: string) {
  if (status === "failed" || status === "rejected") return "error";
  if (
    ["waiting_review", "waiting_input", "degraded", "expired"].includes(status)
  )
    return "warning";
  if (status === "succeeded") return "success";
  if (activeStatuses.has(status)) return "processing";
  return "default";
}
