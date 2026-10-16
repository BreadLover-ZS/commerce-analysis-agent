import {
  Alert,
  Button,
  Checkbox,
  Drawer,
  Input,
  Select,
  Tag,
  App as AntApp,
} from "antd";
import { useEffect, useRef, useState } from "react";
import { api, apiUrl, setAuth } from "./lib/api";
import TaskReport from "./TaskReport";
import WorkspaceSidebar from "./WorkspaceSidebar";
import Icon from "./Icon";
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
  const { message } = AntApp.useApp();
  const contentRef = useRef<HTMLElement>(null);
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

  function openTask(id: string | null) {
    if (id !== null && id === selectedId) return;
    setTask(null);
    setEvents([]);
    setEvidence([]);
    setOpenedEvidence(null);
    setError("");
    setSelectedId(id);
    if (!id) {
      setConversation(null);
      setUploads([]);
    }
    window.scrollTo({ top: 0 });
    contentRef.current?.focus({ preventScroll: true });
  }

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

  if (!authReady)
    return (
      <main className="loading" role="status">
        正在连接工作台…
      </main>
    );
  if (!user)
    return (
      <main className="login-shell">
        <div className="login-brand">
          <span className="brand-mark">
            <Icon name="chart" size={22} />
          </span>
          <span>电商运营分析助手</span>
        </div>
        <section className="login-intro">
          <span className="section-kicker">经营分析工作台</span>
          <h1>
            每一次复盘，
            <br />
            都有据可循。
          </h1>
          <p>
            整理经营数据，核对变化原因，
            <br />
            让下一步行动有清晰的依据。
          </p>
          <ol className="login-workflow">
            <li>
              <span>01</span>导入数据<small>预览并确认本次分析的数据版本</small>
            </li>
            <li>
              <span>02</span>查看分析<small>经营变化、库存风险与竞品资料</small>
            </li>
            <li>
              <span>03</span>核对与导出
              <small>回到原始证据，复核后保留报告</small>
            </li>
          </ol>
        </section>
        <section className="login-card">
          <div>
            <h2>登录工作台</h2>
            <p>使用你的账户继续分析。</p>
          </div>
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
          <p className="login-footnote">数据与报告按账户独立保存</p>
        </section>
        <footer className="login-footer">
          COMMERCE ANALYSIS <span>数据 · 分析 · 决策</span>
        </footer>
      </main>
    );
  return (
    <div className="workspace">
      <a className="skip-link" href="#main-content">
        跳到主要内容
      </a>
      <WorkspaceSidebar
        tasks={tasks}
        selectedId={selectedId}
        email={user.email}
        onSelect={openTask}
        onSignOut={signOut}
      />
      <div className="workspace-content">
        <div className="topbar">
          <span className="breadcrumb">
            工作台 <span>/</span>{" "}
            <strong>{selectedId ? "任务详情" : "新建分析"}</strong>
          </span>
          <span
            className={`mode-badge ${config?.mode === "demo" ? "is-demo" : ""}`}
          >
            <span />
            {config
              ? config.mode === "demo"
                ? "离线演示"
                : "真实模型"
              : "正在读取配置"}
          </span>
        </div>
        <main
          className="main-panel"
          id="main-content"
          tabIndex={-1}
          ref={contentRef}
        >
          <header className="page-header">
            <div>
              <h1>{selectedId ? "分析详情" : "新建分析"}</h1>
              <p>
                {selectedId
                  ? "查看报告、核对证据与跟进执行进度。"
                  : "选择数据与分析范围，开始一次经营复盘。"}
              </p>
            </div>
            {selectedId ? (
              <Button
                icon={<Icon name="plus" />}
                onClick={() => openTask(null)}
              >
                新建分析
              </Button>
            ) : (
              <span className="page-caption">
                <Icon name="file" />
                报告与证据自动保留
              </span>
            )}
          </header>
          {config?.mode === "demo" && (
            <Alert
              type="info"
              showIcon
              message="离线模式未调用模型或真实搜索；竞品资料为模拟，经营数据性质以证据标记为准。"
            />
          )}
          {selectedId && task?.parameters.dataset_id && (
            <p className="dataset-binding">
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
          {!selectedId && (
            <div className="analysis-setup">
              <section className="task-type-section">
                <div className="section-heading">
                  <div>
                    <span className="section-kicker">分析设置</span>
                    <h2>本次要分析什么？</h2>
                  </div>
                  <span>
                    {profile === "commerce_review" ? "01 / 03" : "01 / 02"}
                  </span>
                </div>
                <label className="task-type-label">
                  任务类型
                  <select
                    aria-label="任务类型"
                    value={profile}
                    onChange={(e) => setProfile(e.target.value)}
                  >
                    <option value="commerce_review">
                      经营复盘（固定流程）
                    </option>
                    <option value="research">库存与竞品研究（高级）</option>
                  </select>
                </label>
                <p className="field-hint">
                  {profile === "commerce_review"
                    ? "基于指定数据版本，复盘销售、商品贡献和退款变化。"
                    : "结合销售、库存、内部附件与公开资料，开展专题研究。"}
                </p>
              </section>
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
                  <div>
                    <span className="section-kicker">
                      {profile === "commerce_review" ? "分析要求" : "研究范围"}
                    </span>
                    <h2>定义分析任务</h2>
                  </div>
                  <span>
                    {profile === "commerce_review" ? "03 / 03" : "02 / 02"}
                  </span>
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
                <label className="query-label">
                  分析要求
                  <Input.TextArea
                    aria-label="研究任务"
                    rows={3}
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    maxLength={2000}
                  />
                </label>
                <div
                  className={`form-grid ${profile === "commerce_review" ? "date-grid" : ""}`}
                >
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
                          {
                            value: "workflow",
                            label: "固定资料流程（对比基线）",
                          },
                          { value: "single", label: "集中分析" },
                          {
                            value: "multi",
                            label: "专家分工（可能增加耗时与成本）",
                          },
                        ]}
                      />
                    </label>
                  )}
                </div>
                {profile === "research" && (
                  <Checkbox.Group
                    value={sources}
                    onChange={(values) => setSources(values as Source[])}
                    options={Object.entries(sourceLabels).map(
                      ([value, label]) => ({
                        value,
                        label,
                      }),
                    )}
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
                        checkedDataset !==
                          `${datasetId}:${startDate}:${endDate}`)
                    }
                    onClick={submit}
                  >
                    开始分析
                  </Button>
                </div>
                {uploads.length > 0 && (
                  <div className="uploads">
                    当前研究附件：{uploads.join("、")}
                  </div>
                )}
                {profile === "commerce_review" && !checkedDataset && (
                  <p className="field-hint">
                    请先确认数据版本，并通过所选周期的数据质量检查。
                  </p>
                )}
                <small className="muted">
                  {profile === "commerce_review"
                    ? "经营CSV最多2 MiB、100个商品、10000条记录；日期跨度最多366天。缺失记录不会补零。"
                    : "样例销售周期为2026-08-02至2026-09-30，库存快照为2026-09-30；内部附件每个最多5MB。"}
                </small>
              </section>
            </div>
          )}
          {selectedId && task && (
            <TaskReport
              task={task}
              events={events}
              evidence={evidence}
              busy={busy}
              action={action}
              onOpenEvidence={setOpenedEvidence}
            />
          )}
          {selectedId && !task && (
            <div className="loading" role="status">
              正在加载任务详情…
            </div>
          )}
        </main>
      </div>
      <Drawer
        title={openedEvidence?.title}
        open={!!openedEvidence}
        onClose={() => setOpenedEvidence(null)}
        width={640}
        rootClassName="evidence-drawer"
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
