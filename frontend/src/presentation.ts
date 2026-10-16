export const activeStatuses = new Set([
  "queued",
  "running",
  "cancelling",
  "waiting_review",
]);

export const statusLabels: Record<string, string> = {
  queued: "排队中",
  running: "分析中",
  cancelling: "取消中",
  waiting_review: "待复核",
  succeeded: "已完成",
  degraded: "已完成 · 有信息缺口",
  failed: "执行失败",
  cancelled: "已取消",
  rejected: "已驳回",
};

export function statusColor(status: string) {
  if (status === "failed" || status === "rejected") return "error";
  if (status === "waiting_review" || status === "degraded") return "warning";
  if (status === "succeeded") return "success";
  if (activeStatuses.has(status)) return "processing";
  return "default";
}
