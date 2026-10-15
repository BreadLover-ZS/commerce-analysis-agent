import { Alert } from "antd";
import type { Task, TaskEvent } from "./types";

const categories: Record<string, { title: string; action: string }> = {
  timeout: {
    title: "模型请求超时",
    action: "请核对模型服务状态后，再决定是否重新提交。",
  },
  authentication: {
    title: "模型服务认证失败",
    action: "请联系服务配置人员核对密钥与访问权限。",
  },
  rate_limit: {
    title: "模型服务请求受限",
    action: "请等待服务方限制解除后，再决定是否重新提交。",
  },
  service_unavailable: {
    title: "模型服务暂时无法完成请求",
    action: "请核对服务方状态，稍后再决定是否重新提交。",
  },
  request_rejected: {
    title: "模型服务拒绝了请求",
    action: "请联系服务配置人员核对模型名称及请求兼容性。",
  },
  connection: {
    title: "无法完成与模型服务的连接",
    action: "请核对服务地址和网络连通性。",
  },
};
const codes: Record<string, string> = {
  APITimeoutError: "timeout",
  AuthenticationError: "authentication",
  PermissionDeniedError: "authentication",
  RateLimitError: "rate_limit",
  InternalServerError: "service_unavailable",
  BadRequestError: "request_rejected",
  APIConnectionError: "connection",
};
const phases: Record<string, string> = {
  connect: "建立连接",
  read: "等待响应数据",
  write: "发送请求数据",
  pool: "等待可用连接",
};

interface FailureDetails {
  title: string;
  action: string;
  phase?: string;
  elapsed?: string;
}

function failureDetails(task: Task, events: TaskEvent[]): FailureDetails {
  const code = task.error_code || "unknown";
  // Terminal task limits take precedence over errors caught earlier in the run.
  if (code === "runtime_budget")
    return {
      title: "任务达到执行总时限",
      action:
        "任务已停止。请查看已保存的事件与证据，缩小分析范围后再决定是否提交新任务。",
    };
  if (code === "model_or_tool_budget")
    return {
      title: "任务达到调用或用量限制",
      action: "任务已停止。请核对本次调用与用量记录后，再决定是否提交新任务。",
    };
  if (code === "recovery_exhausted")
    return {
      title: "任务恢复次数已用尽",
      action: "请查看执行事件，联系维护人员核对故障原因与已保存的产物。",
    };
  const latest = events
    .filter(
      (event) => event.kind === "model_error" && event.data.error_type === code,
    )
    .reduce<
      TaskEvent | undefined
    >((last, event) => (!last || event.id > last.id ? event : last), undefined);
  const category =
    typeof latest?.data.category === "string" &&
    Object.hasOwn(categories, latest.data.category)
      ? latest.data.category
      : Object.hasOwn(codes, code)
        ? codes[code]
        : "unknown";
  const details = Object.hasOwn(categories, category)
    ? categories[category]
    : {
        title: "任务未能完成",
        action: "请查看已保存的执行事件与证据，并联系维护人员核对错误代码。",
      };
  const phase = category === "timeout" ? latest?.data.timeout_phase : undefined;
  const elapsed = latest?.data.elapsed_ms;
  return {
    ...details,
    phase:
      category === "timeout"
        ? typeof phase === "string" && Object.hasOwn(phases, phase)
          ? `超时阶段：${phases[phase]}。`
          : "超时阶段：尚无法确认。"
        : undefined,
    elapsed:
      typeof elapsed === "number" && Number.isFinite(elapsed) && elapsed >= 0
        ? `本次模型请求耗时约 ${(elapsed / 1000).toFixed(1)} 秒。`
        : undefined,
  };
}

export default function TaskDiagnostics({
  task,
  events,
}: {
  task: Task;
  events: TaskEvent[];
}) {
  const failure =
    task.status === "failed" ? failureDetails(task, events) : null;
  const incompleteUsage =
    !task.metrics.usage_complete &&
    task.metrics.model_calls > 0 &&
    ["failed", "degraded", "cancelled"].includes(task.status);
  const researchFailed =
    task.report && task.validation?.research_validation?.passed === false;
  return (
    <div className="task-diagnostics" aria-label="任务诊断">
      {failure && (
        <Alert
          type="error"
          showIcon
          message={failure.title}
          description={
            <>
              <p>{failure.action}</p>
              {failure.phase && <p>{failure.phase}</p>}
              {failure.elapsed && <p>{failure.elapsed}</p>}
              <details>
                <summary>排查信息</summary>
                <p>任务编号：{task.id}</p>
                <p>错误代码：{task.error_code || "unknown"}</p>
              </details>
            </>
          }
        />
      )}
      {incompleteUsage && (
        <Alert
          type="warning"
          showIcon
          message="模型用量不完整，费用尚不能确认"
          description="服务方没有返回完整用量。请先核对服务方记录；重新提交会创建新任务，可能产生额外费用。"
        />
      )}
      {researchFailed && (
        <Alert
          type="warning"
          showIcon
          message="研究结论未通过完整复核"
          description="研究部分已退回原文摘录，未采用未复核的研究推断。请结合证据和信息缺口核对报告，不能将其视为完整模型研究结果。"
        />
      )}
    </div>
  );
}
