import { Alert, Button } from "antd";
import { useEffect, useState } from "react";
import { api } from "./lib/api";
import { statusLabels } from "./presentation";
import type { Evidence, Task } from "./types";

const sorted = (value: string[]) => [...value].sort().join(",");
const budgetKey = (value: Task["parameters"]["budget"]) =>
  value
    ? JSON.stringify(
        Object.entries(value).sort(([a], [b]) => a.localeCompare(b)),
      )
    : "";

export default function DiagnosisComparison({
  task,
  tasks,
  busy,
  onCreate,
  onOpen,
  mode,
}: {
  task: Task;
  tasks: Task[];
  busy: boolean;
  onCreate?: () => Promise<Task | null>;
  onOpen?: (id: string) => void;
  mode?: string;
}) {
  const candidates = tasks.filter(
    (row) =>
      row.parameters.profile === "commerce_review" &&
      row.parameters.bundle_id === task.parameters.bundle_id &&
      !!row.parameters.bundle_id,
  );
  const [selected, setSelected] = useState("");
  const id = selected || candidates[0]?.id || "";
  const listed = candidates.find((row) => row.id === id);
  const [pair, setPair] = useState<{
    task: Task;
    source: Evidence | null;
  } | null>(null);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let current = true;
    setPair(null);
    setError("");
    if (!id) return;
    void (async () => {
      try {
        // Read detail before evidence so every refresh passes source authorization.
        const detail = await api<Task>(`/api/tasks/${id}`);
        const evidence = await api<{ evidence: Evidence[] }>(
          `/api/tasks/${id}/evidence`,
        );
        if (current)
          setPair({
            task: detail,
            source:
              evidence.evidence.find((e) => e.payload.tool === "sales") || null,
          });
      } catch (e) {
        if (current) setError((e as Error).message);
      }
    })();
    return () => {
      current = false;
    };
  }, [id, listed?.status, listed?.metrics.tool_calls, refresh]);
  const report = task.report && "profile" in task.report ? task.report : null;
  const source = pair?.source?.payload as
    | {
        bundle_id?: string;
        bundle_hash?: string;
        scope?: { skus: string[] };
        period?: { start: string; end: string };
        previous_period?: { start: string; end: string; comparable: boolean };
        metrics?: Record<string, string>;
      }
    | undefined;
  const same = {
    input:
      !!source?.bundle_hash &&
      source.bundle_id === task.parameters.bundle_id &&
      source.bundle_hash === report?.bundle_hash,
    period:
      !!source?.period &&
      source.period.start === report?.period.start &&
      source.period.end === report?.period.end &&
      source.previous_period?.start === report?.comparison_period.start &&
      source.previous_period?.end === report?.comparison_period.end,
    scope:
      !!source?.scope &&
      !!report &&
      sorted(source.scope.skus) === sorted(report.scope.skus),
    mode: !!pair && pair.task.mode === task.mode,
    budget:
      !!budgetKey(task.parameters.budget) &&
      budgetKey(task.parameters.budget) ===
        budgetKey(pair?.task.parameters.budget),
  };
  const aligned = Object.values(same).every(Boolean);
  const pureSales =
    task.parameters.sources?.length === 1 &&
    task.parameters.sources[0] === "sales";
  const finalScope = report ? sorted(report.scope.skus) : "";
  const currentFact = report?.facts.find(
    (f) => f.metric_key === "net_revenue.current" && f.entity_id === finalScope,
  );
  const deltaFact = report?.facts.find(
    (f) => f.metric_key === "net_revenue.delta" && f.entity_id === finalScope,
  );
  const tokens = (row: Task) =>
    row.metrics.usage_complete
      ? String(row.metrics.input_tokens + row.metrics.output_tokens)
      : "未知";
  async function create() {
    const result = await onCreate?.();
    if (result) setSelected(result.id);
  }
  return (
    <section className="panel diagnosis-comparison" aria-label="同输入流程对照">
      <div className="section-heading">
        <div>
          <h2>同输入流程对照</h2>
          <p>核对共同销售事实与执行开销；业务完成情况仍需逐项审查。</p>
        </div>
      </div>
      <div className="diagnosis-comparison-actions">
        <label>
          固定流程记录
          <select
            aria-label="固定流程对照记录"
            value={id}
            onChange={(e) => setSelected(e.target.value)}
          >
            {!candidates.length && <option value="">尚无同输入记录</option>}
            {candidates.map((row) => (
              <option value={row.id} key={row.id}>
                {row.id.slice(0, 8)} · {statusLabels[row.status] || row.status}
              </option>
            ))}
          </select>
        </label>
        <Button
          disabled={
            busy || !report || !pureSales || mode !== task.mode || !onCreate
          }
          onClick={() => void create()}
        >
          创建同输入固定对照
        </Button>
        {id && (
          <>
            <Button onClick={() => setRefresh((value) => value + 1)}>
              刷新对照
            </Button>
            <Button onClick={() => onOpen?.(id)}>打开固定流程记录</Button>
          </>
        )}
      </div>
      <p className="muted">
        创建对照会新增一项任务，沿用固定输入、问题及复核要求，使用当前运行模式。库存、规则和动态下钻的新增能力应单独核对。
      </p>
      {mode !== task.mode && (
        <Alert
          type="warning"
          message="当前运行模式与原任务不同，不能创建同模式对照。"
        />
      )}
      {error && <Alert type="error" message={error} />}
      {pair && (
        <>
          <p>
            固定流程状态：{statusLabels[pair.task.status] || pair.task.status}；
            {pair.task.validation?.passed
              ? "程序校验通过"
              : "报告尚未通过程序校验"}
            。
          </p>
          <ul aria-label="对照一致性">
            <li>输入版本：{same.input ? "一致" : "待核对"}</li>
            <li>实际两期：{same.period ? "一致" : "待核对"}</li>
            <li>
              最终范围：
              {same.scope ? "一致" : "不同或尚未确定，金额不可直接比较"}
            </li>
            <li>运行模式：{same.mode ? "一致" : "不同"}</li>
            <li>资源上限：{same.budget ? "一致" : "不同或未知"}</li>
          </ul>
          <Alert
            type={aligned ? "info" : "warning"}
            message={
              aligned
                ? "输入和口径已对齐，仍需核对业务完成情况。"
                : "存在尚未对齐的条件，请勿据此判断流程优劣。"
            }
          />
          <div className="comparison-table-wrap">
            <table className="comparison-table">
              <thead>
                <tr>
                  <th>核对项</th>
                  <th>固定流程</th>
                  <th>经营诊断</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <th>已记录净销售额（元）</th>
                  <td>{source?.metrics?.net_revenue ?? "—"}</td>
                  <td>{currentFact?.value ?? "无同范围事实"}</td>
                </tr>
                <tr>
                  <th>可比较变化金额（元）</th>
                  <td>{source?.metrics?.net_revenue_delta ?? "不可比较"}</td>
                  <td>{deltaFact?.value ?? "不可比较"}</td>
                </tr>
                <tr>
                  <th>工具执行</th>
                  <td>{pair.task.metrics.tool_calls}</td>
                  <td>{task.metrics.tool_calls}</td>
                </tr>
                <tr>
                  <th>模型调用</th>
                  <td>{pair.task.metrics.model_calls}</td>
                  <td>{task.metrics.model_calls}</td>
                </tr>
                <tr>
                  <th>Token</th>
                  <td>{tokens(pair.task)}</td>
                  <td>{tokens(task)}</td>
                </tr>
                <tr>
                  <th>累计执行（秒）</th>
                  <td>{(pair.task.metrics.runtime_ms / 1000).toFixed(1)}</td>
                  <td>{(task.metrics.runtime_ms / 1000).toFixed(1)}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p className="muted">
            {task.mode === "demo" && pair.task.mode === "demo"
              ? "本次为离线策略对照，不能证明真实模型效果。"
              : "历史任务的完整模型配置尚未核验，不能宣称模型条件相同。"}{" "}
            费用未核验，不以0代替。单次耗时不代表稳定提速；正式分集配对及独立审查以评测回执为准。
          </p>
        </>
      )}
    </section>
  );
}
