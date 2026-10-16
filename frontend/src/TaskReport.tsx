import { Button, Tag } from "antd";
import { apiUrl } from "./lib/api";
import TaskDiagnostics from "./TaskDiagnostics";
import Icon from "./Icon";
import { activeStatuses, statusLabels, statusColor } from "./presentation";
import type { Evidence, Task, TaskEvent } from "./types";

export default function TaskReport({
  task,
  events,
  evidence,
  busy,
  action,
  onOpenEvidence,
}: {
  task: Task;
  events: TaskEvent[];
  evidence: Evidence[];
  busy: boolean;
  action: (path: string, body?: object) => Promise<void>;
  onOpenEvidence: (evidence: Evidence | null) => void;
}) {
  const running =
    activeStatuses.has(task.status) && task.status !== "waiting_review";
  return (
    <>
      <section className="panel report-paper">
        <div className="section-heading">
          <div>
            <span className="section-kicker">分析报告</span>
            <h2>{task.report?.title || "研究进度"}</h2>
            <p className="muted">{task.query}</p>
            <small>
              统计周期：{task.parameters.start_date} 至{" "}
              {task.parameters.end_date} · CNY · Asia/Shanghai ·{" "}
              {task.mode === "demo" ? "本任务离线执行" : "本任务请求真实模型"}
            </small>
          </div>
          <Tag color={statusColor(task.status)}>
            {statusLabels[task.status]}
          </Tag>
        </div>
        <div className="metrics">
          <span>
            工具执行 <b>{task.metrics.tool_calls}</b>
          </span>
          <span>
            模型调用 <b>{task.metrics.model_calls}</b>
          </span>
          <span>
            累计执行 <b>{(task.metrics.runtime_ms / 1000).toFixed(1)}s</b>
          </span>
          <span>
            Token{" "}
            <b>
              {task.metrics.usage_complete
                ? task.metrics.input_tokens + task.metrics.output_tokens
                : "数据不完整"}
            </b>
          </span>
        </div>
        <TaskDiagnostics task={task} events={events} />
        {running && (
          <Button
            danger
            onClick={() => action(`/api/tasks/${task.id}/cancel`)}
            disabled={busy || task.status === "cancelling"}
          >
            取消任务
          </Button>
        )}
        {task.status === "waiting_review" && (
          <div className="review-actions">
            <p>草稿及证据已保存。请核对重要结论后决定是否导出。</p>
            <Button
              type="primary"
              onClick={() =>
                action(`/api/tasks/${task.id}/review`, { approved: true })
              }
              disabled={busy}
            >
              确认并导出
            </Button>
            <Button
              danger
              onClick={() =>
                action(`/api/tasks/${task.id}/review`, {
                  approved: false,
                })
              }
              disabled={busy}
            >
              驳回草稿
            </Button>
          </div>
        )}
        {!task.report && (
          <div className="report-placeholder" role="status">
            <Icon name={running ? "activity" : "file"} size={28} />
            <h3>{running ? "分析正在进行" : "本次任务暂无报告"}</h3>
            <p>
              {running
                ? "执行进度会自动更新，可先查看下方事件与已采集的证据。"
                : "可在任务诊断与执行事件中查看详情。"}
            </p>
          </div>
        )}
        {!!task.report?.sections.length && (
          <nav className="report-index" aria-label="报告目录">
            {task.report.sections.map((section, index) => (
              <a key={section.name} href={`#report-section-${index}`}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                {section.name}
              </a>
            ))}
          </nav>
        )}
        {task.report?.sections.map((section, sectionIndex) => (
          <section
            className="report-section"
            id={`report-section-${sectionIndex}`}
            key={section.name}
          >
            <h4>{section.name}</h4>
            {section.claims.length === 0 && (
              <p className="muted">当前证据不足，无法形成可靠结论。</p>
            )}
            {section.claims.map((claim) => (
              <div className={`claim claim-${claim.kind}`} key={claim.id}>
                <Tag bordered={false}>
                  {
                    {
                      fact: "事实",
                      analysis: "分析推断",
                      recommendation: "行动建议",
                    }[claim.kind]
                  }
                </Tag>
                <p>{claim.text}</p>
                <div className="citations">
                  {claim.evidence_ids.map((id, i) => (
                    <button
                      type="button"
                      key={id}
                      onClick={() =>
                        onOpenEvidence(
                          evidence.find((e) => e.id === id) || null,
                        )
                      }
                    >
                      依据 {i + 1}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </section>
        ))}
        {!!task.report?.limitations.length && (
          <div className="limitations">
            <h4>信息缺口与适用边界</h4>
            <ul>
              {task.report.limitations.map((text, i) => (
                <li key={i}>{text}</li>
              ))}
            </ul>
          </div>
        )}
        {task.validation && (
          <p className="validation-note">{task.validation.note}</p>
        )}
        {!!task.artifacts?.length && (
          <div className="downloads">
            {task.artifacts.map((file) => (
              <a key={file.id} href={apiUrl(`/api/artifacts/${file.id}`)}>
                {file.name} ↓
              </a>
            ))}
          </div>
        )}
      </section>
      <div className="detail-grid" id="task-details">
        <section className="panel">
          <div className="section-heading">
            <h3>执行事件</h3>
            <span>刷新页面可继续查看</span>
          </div>
          <ol className="event-list">
            {events.length === 0 && <li className="muted">暂无执行事件</li>}
            {events.map((e) => (
              <li key={e.id}>
                <time>
                  {new Date(e.timestamp).toLocaleTimeString("zh-CN", {
                    hour12: false,
                  })}
                </time>
                <span>{e.message}</span>
              </li>
            ))}
          </ol>
        </section>
        <section className="panel">
          <div className="section-heading">
            <h3>证据目录</h3>
            <span>{evidence.length} 项</span>
          </div>
          <div className="evidence-list">
            {evidence.length === 0 && (
              <p className="empty-note">尚未采集到证据。</p>
            )}
            {evidence.map((e) => (
              <button
                type="button"
                key={e.id}
                onClick={() => onOpenEvidence(e)}
              >
                <strong>{e.title}</strong>
                <small>{e.payload.sample ? "模拟资料" : "查看原文片段"}</small>
              </button>
            ))}
          </div>
        </section>
      </div>
    </>
  );
}
