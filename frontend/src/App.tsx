import {
  Alert,
  Button,
  Checkbox,
  Drawer,
  Input,
  Select,
  Tag,
  message,
} from "antd";
import { useEffect, useRef, useState } from "react";
import { api, apiUrl, setAuth } from "./lib/api";
import TaskDiagnostics from "./TaskDiagnostics";
import CommerceDataPanel from "./CommerceDataPanel";
import type { Dataset } from "./CommerceDataPanel";
import type {
  AppConfig,
  Evidence,
  Source,
  Task,
  TaskEvent,
  User,
} from "./types";

const active = new Set(["queued", "running", "cancelling", "waiting_review"]);
const statusLabels: Record<string, string> = {
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
const sourceLabels: Record<Source, string> = {
  sales: "销售与退款",
  inventory: "库存",
  documents: "内部规则与附件",
  web: "公开竞品资料",
};
const examples = [
  {
    title: "月度经营复盘",
    query:
      "分析九月净销售额、退款情况和库存风险，结合内部规则说明下一步需要核对的事项。",
    sources: ["sales", "inventory", "documents"] as Source[],
  },
  {
    title: "竞品与库存综合研究",
    query:
      "分析保温杯与收纳包的经营表现、库存风险和竞品定位，生成有依据的报告，区分事实与建议。",
    sources: ["sales", "inventory", "documents", "web"] as Source[],
  },
  {
    title: "库存诊断",
    query: "检查库存覆盖天数和补货风险，说明计算口径及历史销量估计的局限。",
    sources: ["inventory", "documents"] as Source[],
  },
];

export default function App() {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [authReady, setAuthReady] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [task, setTask] = useState<Task | null>(null);
  const [events, setEvents] = useState<TaskEvent[]>([]);
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [openedEvidence, setOpenedEvidence] = useState<Evidence | null>(null);
  const [conversation, setConversation] = useState<string | null>(null);
  const [profile, setProfile] = useState("commerce_review");
  const [datasetId, setDatasetId] = useState<string | null>(null);
  const [checkedDataset, setCheckedDataset] = useState<string | null>(null);
  const [query, setQuery] = useState(
    "复盘本期经营表现、商品变化贡献和退款情况，区分事实与待验证的原因。",
  );
  const [sources, setSources] = useState<Source[]>(examples[1].sources);
  const [startDate, setStartDate] = useState("2026-09-01");
  const [endDate, setEndDate] = useState("2026-09-30");
  const [publicQuery, setPublicQuery] = useState(
    "保温杯 品牌 官网\n通勤收纳包 品牌 官网",
  );
  const [strategy, setStrategy] = useState("single");
  const [requireReview, setRequireReview] = useState(false);
  const [uploads, setUploads] = useState<string[]>([]);
  const cursor = useRef(0);
  const pendingSubmission = useRef<{ fingerprint: string; key: string } | null>(
    null,
  );

  useEffect(() => {
    const clear = () => {
      setAuth(null);
      setUser(null);
      setTask(null);
      setSelectedId(null);
      setTasks([]);
      setEvidence([]);
      setOpenedEvidence(null);
      setEvents([]);
      setConversation(null);
      setUploads([]);
      setDatasetId(null);
      setCheckedDataset(null);
    };
    window.addEventListener("commerce:unauthorized", clear);
    return () => window.removeEventListener("commerce:unauthorized", clear);
  }, []);

  useEffect(() => {
    api<AppConfig>("/api/config")
      .then(setConfig)
      .catch((e) => setError(e.message));
    api<User>("/api/auth/me")
      .then((u) => {
        setAuth(u);
        setUser(u);
      })
      .catch(() => {})
      .finally(() => setAuthReady(true));
  }, []);

  useEffect(() => {
    if (!user) return;
    let disposed = false;
    const refresh = async () => {
      try {
        const result = await api<{ tasks: Task[] }>("/api/tasks");
        if (!disposed) setTasks(result.tasks);
      } catch (e) {
        if (!disposed) setError((e as Error).message);
      }
    };
    void refresh();
    const timer = window.setInterval(refresh, 2000);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [user]);

  useEffect(() => {
    if (!selectedId || !user) return;
    let disposed = false;
    let fetching = false;
    cursor.current = 0;
    setEvents([]);
    setTask(null);
    setEvidence([]);
    const refresh = async () => {
      if (fetching) return;
      fetching = true;
      try {
        const [detail, history] = await Promise.all([
          api<Task>(`/api/tasks/${selectedId}`),
          api<{ events: TaskEvent[]; cursor: number }>(
            `/api/tasks/${selectedId}/events?after=${cursor.current}`,
          ),
        ]);
        // Reports only reference committed, append-only evidence. Read that ledger
        // after the report so a new report cannot be paired with an earlier snapshot.
        const sourcesResult = await api<{ evidence: Evidence[] }>(
          `/api/tasks/${selectedId}/evidence`,
        );
        if (!disposed) {
          setTask(detail);
          setEvidence(sourcesResult.evidence);
          cursor.current = history.cursor;
          setEvents((prev) => [
            ...new Map(
              [...prev, ...history.events].map((e) => [e.id, e]),
            ).values(),
          ]);
        }
      } catch (e) {
        if (!disposed) setError((e as Error).message);
      } finally {
        fetching = false;
      }
    };
    void refresh();
    const timer = window.setInterval(refresh, 1200);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [selectedId, user]);

  useEffect(() => {
    if (user)
      setSelectedId(
        window.sessionStorage.getItem(`commerce.selected.${user.email}`),
      );
  }, [user]);

  useEffect(() => {
    if (!user) return;
    const key = `commerce.selected.${user.email}`;
    if (selectedId) window.sessionStorage.setItem(key, selectedId);
    else window.sessionStorage.removeItem(key);
  }, [selectedId, user]);

  async function signIn() {
    setBusy(true);
    setError("");
    try {
      const current = await api<User>("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      setAuth(current);
      setUser(current);
      setPassword("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function ensureConversation() {
    if (conversation) return conversation;
    const result = await api<{ id: string }>("/api/conversations", {
      method: "POST",
    });
    setConversation(result.id);
    return result.id;
  }

  function selectDataset(dataset: Dataset) {
    setDatasetId(dataset.id);
    setStartDate(dataset.suggested_start);
    setEndDate(dataset.period_end);
  }

  async function submit() {
    if (!query.trim() || sources.length === 0) return;
    if (
      profile === "commerce_review" &&
      checkedDataset !== `${datasetId}:${startDate}:${endDate}`
    )
      return;
    setBusy(true);
    setError("");
    try {
      const conversationId = await ensureConversation();
      const payload = {
        conversation_id: conversationId,
        query,
        start_date: startDate,
        end_date: endDate,
        profile,
        dataset_id: profile === "commerce_review" ? datasetId : null,
        sources: profile === "commerce_review" ? ["sales"] : sources,
        strategy: profile === "commerce_review" ? "workflow" : strategy,
        require_review: requireReview,
        public_queries:
          profile !== "commerce_review" && sources.includes("web")
            ? publicQuery
                .split("\n")
                .map((s) => s.trim())
                .filter(Boolean)
            : [],
      };
      const fingerprint = JSON.stringify(payload);
      if (pendingSubmission.current?.fingerprint !== fingerprint)
        pendingSubmission.current = { fingerprint, key: crypto.randomUUID() };
      const result = await api<Task>("/api/tasks", {
        method: "POST",
        headers: { "Idempotency-Key": pendingSubmission.current.key },
        body: fingerprint,
      });
      pendingSubmission.current = null;
      setSelectedId(result.id);
      setTasks((prev) => [result, ...prev.filter((t) => t.id !== result.id)]);
      message.success("分析任务已登记");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function action(path: string, body?: object) {
    setBusy(true);
    setError("");
    try {
      const result = await api<Task>(path, {
        method: "POST",
        body: body ? JSON.stringify(body) : undefined,
      });
      setTask(result);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function upload(files: FileList | null) {
    if (!files?.length) return;
    setBusy(true);
    setError("");
    try {
      const id = await ensureConversation();
      const data = new FormData();
      data.append("conversation_id", id);
      Array.from(files).forEach((f) => data.append("files", f));
      const result = await api<{ documents: { name: string }[] }>(
        "/api/documents",
        { method: "POST", body: data },
      );
      setUploads((prev) => [
        ...new Set([...prev, ...result.documents.map((d) => d.name)]),
      ]);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function signOut() {
    try {
      await api("/api/auth/logout", { method: "POST" });
      setAuth(null);
      setUser(null);
      setTasks([]);
      setTask(null);
      setSelectedId(null);
      setEvidence([]);
      setOpenedEvidence(null);
      setEvents([]);
      setConversation(null);
      setUploads([]);
      setDatasetId(null);
      setCheckedDataset(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  if (!authReady) return <main className="loading">正在连接研究工作台…</main>;
  if (!user)
    return (
      <main className="login-shell">
        <section className="login-card">
          <span className="eyebrow">COMMERCE ANALYSIS AGENT</span>
          <h1>电商运营分析助手</h1>
          <p>用经营数据与可追溯证据，形成可核对的分析。</p>
          {config?.demo_accounts && (
            <Alert
              type="info"
              message="本地演示账户：demo@example.com / demo-commerce-2026"
              description="经营数据与离线竞品资料均为模拟样例。"
            />
          )}
          {error && <Alert type="error" message={error} />}
          <label>
            邮箱
            <Input
              aria-label="邮箱"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="username"
            />
          </label>
          <label>
            密码
            <Input.Password
              aria-label="密码"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              onPressEnter={signIn}
            />
          </label>
          <Button type="primary" block loading={busy} onClick={signIn}>
            登录工作台
          </Button>
        </section>
      </main>
    );

  const running =
    task && active.has(task.status) && task.status !== "waiting_review";
  return (
    <div className="workspace">
      <aside className="sidebar">
        <span className="eyebrow">COMMERCE ANALYSIS AGENT</span>
        <h1>电商运营分析助手</h1>
        <p className="muted">让结论有据可查</p>
        <Button
          block
          onClick={() => {
            setSelectedId(null);
            setTask(null);
            setConversation(null);
            setUploads([]);
            setError("");
          }}
        >
          新建研究
        </Button>
        <h3>研究记录</h3>
        <nav aria-label="研究记录">
          {tasks.map((t) => (
            <button
              type="button"
              key={t.id}
              className={
                selectedId === t.id ? "task-link selected" : "task-link"
              }
              onClick={() => setSelectedId(t.id)}
            >
              <strong>{t.query}</strong>
              <small>{statusLabels[t.status] || t.status}</small>
            </button>
          ))}
        </nav>
        <div className="account">
          <span>{user.email}</span>
          <Button size="small" onClick={signOut}>
            退出
          </Button>
        </div>
      </aside>
      <main className="main-panel">
        <header className="page-header">
          <div>
            <span className="eyebrow">经营分析 · 库存诊断 · 竞品研究</span>
            <h2>从数据到有依据的建议</h2>
          </div>
          <Tag color={config?.mode === "demo" ? "gold" : "blue"}>
            {config?.mode === "demo" ? "离线演示" : "真实模型"}
          </Tag>
        </header>
        {config?.mode === "demo" && (
          <Alert
            type="info"
            showIcon
            message="离线模式未调用模型或真实搜索；竞品资料为模拟，经营数据性质以证据标记为准。"
          />
        )}
        {task?.parameters.dataset_id && (
          <p className="muted">
            当前报告数据版本：{task.parameters.dataset_id}；执行模式：
            {task.mode === "demo" ? "离线模板" : "真实模型"}
            。数据性质见报告与证据标记。
          </p>
        )}
        {error && (
          <Alert
            type="error"
            closable
            onClose={() => setError("")}
            message={error}
          />
        )}
        <label className="panel">
          任务类型
          <select
            aria-label="任务类型"
            value={profile}
            onChange={(e) => setProfile(e.target.value)}
          >
            <option value="commerce_review">经营复盘（固定流程）</option>
            <option value="research">库存与竞品研究（高级）</option>
          </select>
        </label>
        {profile === "commerce_review" && (
          <CommerceDataPanel
            key={user.email}
            selected={datasetId}
            start={startDate}
            end={endDate}
            onSelect={selectDataset}
            onChecked={setCheckedDataset}
          />
        )}
        <section className="panel composer">
          <div className="section-heading">
            <h3>研究任务</h3>
            <span>明确范围，控制信息来源</span>
          </div>
          {profile === "research" && (
            <div className="examples">
              {examples.map((e) => (
                <button
                  type="button"
                  key={e.title}
                  onClick={() => {
                    setQuery(e.query);
                    setSources(e.sources);
                  }}
                >
                  {e.title}
                </button>
              ))}
            </div>
          )}
          <Input.TextArea
            aria-label="研究任务"
            rows={3}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            maxLength={2000}
          />
          <div className="form-grid">
            <label>
              开始日期
              <input
                aria-label="开始日期"
                type="date"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
              />
            </label>
            <label>
              结束日期
              <input
                aria-label="结束日期"
                type="date"
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
              />
            </label>
            {profile === "research" && (
              <label>
                研究方式
                <Select
                  aria-label="研究方式"
                  value={strategy}
                  onChange={setStrategy}
                  options={[
                    { value: "workflow", label: "固定资料流程（对比基线）" },
                    { value: "single", label: "集中分析" },
                    { value: "multi", label: "专家分工（可能增加耗时与成本）" },
                  ]}
                />
              </label>
            )}
          </div>
          {profile === "research" && (
            <Checkbox.Group
              value={sources}
              onChange={(values) => setSources(values as Source[])}
              options={Object.entries(sourceLabels).map(([value, label]) => ({
                value,
                label,
              }))}
            />
          )}
          {profile === "research" && sources.includes("web") && (
            <label className="public-query">
              公开搜索关键词（每行一条，最多三条）
              <Input.TextArea
                aria-label="公开搜索关键词"
                rows={2}
                value={publicQuery}
                onChange={(e) => setPublicQuery(e.target.value)}
              />
              <small>
                仅这些关键词会发送到公开搜索；请填写可公开的品牌、品类或产品名称。
              </small>
            </label>
          )}
          <div className="composer-actions">
            {profile === "research" && (
              <label className="upload-label">
                添加内部附件
                <input
                  aria-label="添加内部附件"
                  type="file"
                  multiple
                  accept=".md,.txt,.pdf,.docx,.xlsx"
                  onChange={(e) => {
                    void upload(e.target.files);
                    e.target.value = "";
                  }}
                  disabled={busy}
                />
              </label>
            )}
            <Checkbox
              checked={requireReview}
              onChange={(e) => setRequireReview(e.target.checked)}
            >
              导出前由我复核草稿
            </Checkbox>
            <Button
              type="primary"
              loading={busy}
              disabled={
                !query.trim() ||
                !sources.length ||
                (profile === "commerce_review" &&
                  checkedDataset !== `${datasetId}:${startDate}:${endDate}`)
              }
              onClick={submit}
            >
              开始分析
            </Button>
          </div>
          {uploads.length > 0 && (
            <div className="uploads">当前研究附件：{uploads.join("、")}</div>
          )}
          <small className="muted">
            {profile === "commerce_review"
              ? "经营CSV最多2 MiB、100个商品、10000条记录；日期跨度最多366天。缺失记录不会补零。"
              : "样例销售周期为2026-08-02至2026-09-30，库存快照为2026-09-30；内部附件每个最多5MB。"}
          </small>
        </section>
        {task && (
          <>
            <section className="panel">
              <div className="section-heading">
                <div>
                  <h3>{task.report?.title || "研究进度"}</h3>
                  <p className="muted">{task.query}</p>
                  <small>
                    统计周期：{task.parameters.start_date} 至{" "}
                    {task.parameters.end_date} · CNY · Asia/Shanghai ·{" "}
                    {task.mode === "demo"
                      ? "本任务离线执行"
                      : "本任务请求真实模型"}
                  </small>
                </div>
                <Tag color={task.status === "failed" ? "red" : "cyan"}>
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
              {task.report?.sections.map((section) => (
                <section className="report-section" key={section.name}>
                  <h4>{section.name}</h4>
                  {section.claims.length === 0 && (
                    <p className="muted">当前证据不足，无法形成可靠结论。</p>
                  )}
                  {section.claims.map((claim) => (
                    <div className="claim" key={claim.id}>
                      <Tag>
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
                              setOpenedEvidence(
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
            <div className="detail-grid">
              <section className="panel">
                <div className="section-heading">
                  <h3>执行事件</h3>
                  <span>刷新页面可继续查看</span>
                </div>
                <ol className="event-list">
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
                  {evidence.map((e) => (
                    <button
                      type="button"
                      key={e.id}
                      onClick={() => setOpenedEvidence(e)}
                    >
                      <strong>{e.title}</strong>
                      <small>
                        {e.payload.sample ? "模拟资料" : "查看原文片段"}
                      </small>
                    </button>
                  ))}
                </div>
              </section>
            </div>
          </>
        )}
        {!task && (
          <p className="empty-state">
            选择一个研究记录，或开始新的分析。结果将保留可核对的指标与来源。
          </p>
        )}
      </main>
      <Drawer
        title={openedEvidence?.title}
        open={!!openedEvidence}
        onClose={() => setOpenedEvidence(null)}
        width={640}
      >
        {openedEvidence && (
          <div className="evidence-detail">
            <Tag>
              {openedEvidence.payload.sample ? "模拟资料" : openedEvidence.kind}
            </Tag>
            <p>
              获取时间：
              {new Date(openedEvidence.retrieved_at).toLocaleString("zh-CN")}
            </p>
            <p>
              来源定位：
              {openedEvidence.locator.startsWith("https://") ||
              openedEvidence.locator.startsWith("http://") ? (
                <a
                  href={openedEvidence.locator}
                  target="_blank"
                  rel="noreferrer"
                >
                  {openedEvidence.locator}
                </a>
              ) : (
                openedEvidence.locator
              )}
            </p>
            <pre>{openedEvidence.excerpt}</pre>
            <small>
              证据ID：{openedEvidence.id}
              <br />
              内容哈希：{openedEvidence.content_hash}
            </small>
          </div>
        )}
      </Drawer>
    </div>
  );
}
