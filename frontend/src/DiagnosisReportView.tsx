import { Alert, Button, Input, Tag } from "antd";
import { useState } from "react";
import { api, apiUrl } from "./lib/api";
import { activeStatuses, statusColor, statusLabels } from "./presentation";
import TaskDiagnostics from "./TaskDiagnostics";
import DiagnosisComparison from "./DiagnosisComparison";
import type { DiagnosisProjection, Evidence, Task, TaskEvent } from "./types";

const stops: Record<string, string> = {
  target_covered: "本轮核查证据已覆盖，观察仍不证明经营因果",
  exploration_complete: "本轮探索结束，开放问题保留",
  evidence_gap: "固定输入存在缺口，需要补充资料后新建任务",
  no_information: "连续步骤没有取得新信息",
  step_budget: "达到步骤上限",
  tool_budget: "工具额度不足",
  model_budget: "模型额度不足",
  usage_unknown: "模型结果或用量未知，停止新请求",
  invalid_decision: "连续提议未通过校验",
  no_evidence: "没有可核对事实",
};
const verdicts: Record<string, string> = {
  supported_observation: "支持该项观察",
  contradicted: "不支持该项观察",
  insufficient: "证据不足",
};
export interface DiagnosisViewProps {
  task: Task;
  events: TaskEvent[];
  evidence: Evidence[];
  busy: boolean;
  action: (path: string, body?: object) => Promise<void>;
  onOpenEvidence: (evidence: Evidence | null) => void;
  projection?: DiagnosisProjection | null;
  onFollowup?: (query: string) => Promise<void>;
  tasks?: Task[];
  currentMode?: string;
  onCreateComparison?: () => Promise<Task | null>;
  onOpenTask?: (id: string) => void;
}

export default function DiagnosisReportView({
  task,
  events,
  evidence,
  busy,
  action,
  onOpenEvidence,
  projection,
  onFollowup,
  tasks = [],
  currentMode,
  onCreateComparison,
  onOpenTask,
}: DiagnosisViewProps) {
  const report = task.report && "profile" in task.report ? task.report : null;
  const [choice, setChoice] = useState("");
  const [followup, setFollowup] = useState("");
  const [feedback, setFeedback] = useState("");
  const [category, setCategory] = useState("useful");
  const [finding, setFinding] = useState("");
  const [feedbackStatus, setFeedbackStatus] = useState("");
  const [feedbackBusy, setFeedbackBusy] = useState(false);
  const coherent =
    projection?.task_id === task.id &&
    projection.state_version === task.state_version;
  const pending = coherent ? projection.pending_input : null;
  const open = (id: string) =>
    onOpenEvidence(evidence.find((source) => source.id === id) || null);
  async function sendFeedback() {
    if (!projection) return;
    setFeedbackBusy(true);
    setFeedbackStatus("");
    try {
      await api(`/api/tasks/${task.id}/feedback`, {
        method: "POST",
        body: JSON.stringify({
          expected_version: projection.state_version,
          category,
          comment: feedback,
          finding_id: finding || null,
        }),
      });
      setFeedbackStatus("反馈已保存，原报告未修改。");
      setFeedback("");
    } catch (e) {
      setFeedbackStatus((e as Error).message);
    } finally {
      setFeedbackBusy(false);
    }
  }
  return (
    <>
      <section
        className="panel report-paper diagnosis-report"
        aria-label="经营诊断详情"
      >
        <div className="section-heading">
          <div>
            <span className="section-kicker">经营诊断</span>
            <h2>{report?.title || "诊断进度"}</h2>
            <p>{task.query}</p>
          </div>
          <Tag color={statusColor(task.status)}>
            {statusLabels[task.status] || task.status}
          </Tag>
        </div>
        <p>
          {task.mode === "demo"
            ? "离线策略演示，未调用真实模型。"
            : "本任务使用真实模型。"}{" "}
          资料性质以固定输入与证据标记为准。
        </p>
        <div className="metrics">
          <span>
            工具执行 <b>{task.metrics.tool_calls}</b> / 10
          </span>
          <span>
            模型调用 <b>{task.metrics.model_calls}</b> / 12
          </span>
          <span>
            累计执行 <b>{(task.metrics.runtime_ms / 1000).toFixed(1)}s</b>
          </span>
          <span>
            Token{" "}
            <b>
              {task.metrics.usage_complete
                ? task.metrics.input_tokens + task.metrics.output_tokens
                : "用量未知"}
            </b>
          </span>
        </div>
        <TaskDiagnostics task={task} events={events} />
        {activeStatuses.has(task.status) && (
          <Button
            danger
            disabled={busy || task.status === "cancelling"}
            onClick={() => action(`/api/tasks/${task.id}/cancel`)}
          >
            取消任务
          </Button>
        )}
        {task.status === "waiting_input" && (
          <div className="review-actions" aria-label="诊断澄清">
            <h3>需要确认诊断范围</h3>
            {pending?.type === "clarification" ? (
              <>
                <p>{pending.payload.question}</p>
                <label>
                  澄清选项
                  <select
                    aria-label="澄清选项"
                    value={choice}
                    onChange={(e) => setChoice(e.target.value)}
                  >
                    <option value="">请选择原范围内的选项</option>
                    {pending.payload.options?.map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </select>
                </label>
                <p>
                  答复期限：
                  {new Date(pending.waiting_until).toLocaleString("zh-CN")}
                  ；答复后沿用原输入和已消耗额度。
                </p>
                <Button
                  type="primary"
                  disabled={busy || !pending.payload.options?.includes(choice)}
                  onClick={() =>
                    action(`/api/tasks/${task.id}/clarifications`, {
                      interrupt_id: pending.interrupt_id,
                      expected_version: pending.expected_version,
                      choice,
                    })
                  }
                >
                  确认范围并继续
                </Button>
              </>
            ) : (
              <p role="status">正在核对最新澄清版本…</p>
            )}
          </div>
        )}
        {task.status === "waiting_review" && (
          <div className="review-actions">
            <p>
              核对具体报告版本后决定是否导出。复核不会确认未核验解读，也不授予业务操作权限。
            </p>
            {pending?.type === "report_review" &&
            pending.payload.report_hash === task.report_hash ? (
              <>
                <Button
                  type="primary"
                  disabled={busy}
                  onClick={() =>
                    action(`/api/tasks/${task.id}/review`, {
                      approved: true,
                      interrupt_id: pending.interrupt_id,
                      expected_version: pending.expected_version,
                      report_hash: task.report_hash,
                    })
                  }
                >
                  确认并导出
                </Button>
                <Button
                  danger
                  disabled={busy}
                  onClick={() =>
                    action(`/api/tasks/${task.id}/review`, {
                      approved: false,
                      interrupt_id: pending.interrupt_id,
                      expected_version: pending.expected_version,
                      report_hash: task.report_hash,
                    })
                  }
                >
                  驳回草稿
                </Button>
              </>
            ) : (
              <p role="status">正在核对最新报告版本…</p>
            )}
          </div>
        )}
        {!report && (
          <p role="status">
            {activeStatuses.has(task.status)
              ? "诊断步骤与证据会自动更新；刷新后仍可继续。"
              : "本次任务暂无可交付报告，请查看任务诊断与事件。"}
          </p>
        )}
        {report && (
          <>
            <section className="report-section">
              <h3>固定输入与本次范围</h3>
              <p>
                本期：{report.period.start} 至 {report.period.end}；对比期：
                {report.comparison_period.start} 至{" "}
                {report.comparison_period.end}
              </p>
              <p>
                当前范围：{report.scope.skus.join("、")}
                。每条事实保留自身范围，澄清前的基线不会改写。
              </p>
              <p>
                报告版本：{report.report_version}；输入组合：
                <span className="hash-value">{report.bundle_id}</span>
              </p>
              <details>
                <summary>查看版本与校验信息</summary>
                <p className="hash-value">
                  输入哈希：{report.bundle_hash}
                  <br />
                  报告哈希：{task.report_hash}
                </p>
                <pre>{JSON.stringify(report.versions, null, 2)}</pre>
              </details>
            </section>
            <section className="report-section">
              <h3>经营事实与库存代理</h3>
              {report.facts.length === 0 && (
                <p>没有可展示的数值事实；规则原文见下方。</p>
              )}
              {report.facts.map((fact) => (
                <div
                  className="claim claim-fact"
                  key={`${fact.evidence_id}:${fact.metric_key}`}
                >
                  <p>{fact.text}</p>
                  <div className="citations">
                    <button
                      type="button"
                      onClick={() => open(fact.evidence_id)}
                    >
                      核对事实依据
                    </button>
                    <small>{fact.metric_key}</small>
                  </div>
                </div>
              ))}
            </section>
          </>
        )}
        <section className="report-section" aria-label="诊断步骤">
          <h3>诊断过程</h3>
          <ol className="diagnosis-step-list">
            {(projection?.steps || report?.steps || []).map((step) => (
              <li key={step.step_id}>
                <strong>
                  步骤 {step.sequence} · {step.proposal.question}
                </strong>
                <p>
                  {step.proposal.tool_name || step.proposal.action} ·{" "}
                  {step.status}
                  {step.assessment?.hypothesis_status
                    ? ` · ${verdicts[step.assessment.hypothesis_status] || step.assessment.hypothesis_status}`
                    : ""}
                </p>
                {step.proposal.expected_evidence && (
                  <p>拟核对：{step.proposal.expected_evidence}</p>
                )}
                <div className="citations">
                  {step.evidence_ids.map((id) => (
                    <button type="button" key={id} onClick={() => open(id)}>
                      查看步骤证据
                    </button>
                  ))}
                </div>
              </li>
            ))}
          </ol>
          {report && (
            <Alert
              type="info"
              message={`停止原因：${stops[report.stop_reason] || report.stop_reason}`}
            />
          )}
        </section>
        {report && (
          <>
            <section className="report-section">
              <h3>观察与候选解释</h3>
              {report.observations.map((value) => (
                <div className="claim" key={value.finding_id}>
                  <Tag>{verdicts[value.status] || value.status}</Tag>
                  <p>{value.text}</p>
                  {value.missing_data.length > 0 && (
                    <p>尚缺：{value.missing_data.join("；")}</p>
                  )}
                  <div className="citations">
                    {[
                      ...value.supporting_evidence,
                      ...value.contradicting_evidence,
                    ].map((id) => (
                      <button type="button" key={id} onClick={() => open(id)}>
                        核对观察依据
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </section>
            {!!report.rule_quotes?.length && (
              <section className="report-section">
                <h3>规则原文与未核验解读</h3>
                {report.rule_quotes.map((quote, index) => (
                  <div key={`${quote.evidence_id}:${index}`} className="claim">
                    <Tag>原文引用 · 解读未核验</Tag>
                    <strong>{quote.document_name}</strong>
                    <blockquote>{quote.quote}</blockquote>
                    <div className="citations">
                      <button
                        type="button"
                        onClick={() => open(quote.evidence_id)}
                      >
                        核对逐字原文
                      </button>
                    </div>
                  </div>
                ))}
                {report.rule_interpretations?.map((value, index) => (
                  <div className="claim" key={index}>
                    <Tag color="warning">未核验解读</Tag>
                    <p>{value.text}</p>
                    <div className="citations">
                      {value.references.map((ref, i) => (
                        <button
                          type="button"
                          key={i}
                          onClick={() => open(ref.evidence_id)}
                        >
                          原文引用 {i + 1}
                        </button>
                      ))}
                    </div>
                  </div>
                ))}
                {report.rule_conflicts?.map((candidate, index) => (
                  <Alert
                    key={index}
                    type="warning"
                    message="未核验冲突候选"
                    description={candidate.reason}
                  />
                ))}
              </section>
            )}
            <section className="report-section">
              <h3>核对建议与未完成问题</h3>
              <ul>
                {[...report.recommendations, ...report.open_questions].map(
                  (text, index) => (
                    <li key={index}>{text}</li>
                  ),
                )}
              </ul>
            </section>
            <div className="limitations">
              <h3>资料限制与适用边界</h3>
              <ul>
                {report.limitations.map((text, index) => (
                  <li key={index}>{text}</li>
                ))}
              </ul>
            </div>
            <p>
              数值与观察：
              {task.validation?.passed ? "通过固定来源重算门禁" : "未通过"}
              。规则语义与经营原因仍需人工核对。
            </p>
            {!!task.artifacts?.length && (
              <div className="downloads">
                {task.artifacts.map((file) => (
                  <a key={file.id} href={apiUrl(`/api/artifacts/${file.id}`)}>
                    {file.name} ↓
                  </a>
                ))}
              </div>
            )}
          </>
        )}
      </section>
      {report && (
        <div className="detail-grid">
          <section className="panel">
            <h3>对该固定输入追问</h3>
            <p>创建新任务，沿用原输入、周期与来源，保留本次报告。</p>
            <Input.TextArea
              aria-label="诊断追问"
              value={followup}
              maxLength={2000}
              onChange={(e) => setFollowup(e.target.value)}
            />
            <Button
              disabled={busy || followup.trim().length < 2 || !onFollowup}
              onClick={() => onFollowup?.(followup)}
            >
              创建追问任务
            </Button>
          </section>
          <section className="panel">
            <h3>报告反馈</h3>
            <label>
              反馈类型
              <select
                aria-label="反馈类型"
                value={category}
                onChange={(e) => setCategory(e.target.value)}
              >
                {Object.entries({
                  useful: "有帮助",
                  incorrect_fact: "事实有误",
                  missing_data: "缺少数据",
                  unclear: "表达不清",
                  other: "其他",
                }).map(([value, text]) => (
                  <option value={value} key={value}>
                    {text}
                  </option>
                ))}
              </select>
            </label>
            <label>
              对应观察
              <select
                aria-label="对应观察"
                value={finding}
                onChange={(e) => setFinding(e.target.value)}
              >
                <option value="">整份报告</option>
                {report.observations.map((value) => (
                  <option value={value.finding_id} key={value.finding_id}>
                    {value.text.slice(0, 60)}
                  </option>
                ))}
              </select>
            </label>
            <Input.TextArea
              aria-label="反馈说明"
              maxLength={1000}
              value={feedback}
              onChange={(e) => setFeedback(e.target.value)}
            />
            <Button
              disabled={!coherent || busy || feedbackBusy}
              onClick={() => void sendFeedback()}
            >
              提交反馈
            </Button>
            {feedbackStatus && <p role="status">{feedbackStatus}</p>}
          </section>
        </div>
      )}
      <div className="detail-grid">
        <section className="panel">
          <h3>执行事件</h3>
          <ol className="event-list">
            {events.map((event) => (
              <li key={event.id}>
                <time>
                  {new Date(event.timestamp).toLocaleTimeString("zh-CN", {
                    hour12: false,
                  })}
                </time>
                <span>{event.message}</span>
              </li>
            ))}
          </ol>
        </section>
        <section className="panel">
          <h3>证据目录</h3>
          <div className="evidence-list">
            {evidence.map((source) => (
              <button
                type="button"
                key={source.id}
                onClick={() => onOpenEvidence(source)}
              >
                <strong>{source.title}</strong>
                <small>查看完整固定来源与计算结果</small>
              </button>
            ))}
          </div>
        </section>
      </div>
      <DiagnosisComparison
        task={task}
        tasks={tasks}
        busy={busy}
        mode={currentMode}
        onCreate={onCreateComparison}
        onOpen={onOpenTask}
      />
    </>
  );
}
