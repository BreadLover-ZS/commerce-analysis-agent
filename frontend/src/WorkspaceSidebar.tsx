import { Button, Input } from "antd";
import { useState } from "react";
import Icon from "./Icon";
import { activeStatuses, statusLabels } from "./presentation";
import type { Task } from "./types";

export default function WorkspaceSidebar({
  tasks,
  selectedId,
  email,
  onSelect,
  onSignOut,
}: {
  tasks: Task[];
  selectedId: string | null;
  email: string;
  onSelect: (id: string | null) => void;
  onSignOut: () => Promise<void>;
}) {
  const [search, setSearch] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const filtered = tasks.filter((task) =>
    task.query.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()),
  );
  return (
    <aside className={`sidebar ${historyOpen ? "history-open" : ""}`}>
      <div className="brand">
        <span className="brand-mark">
          <Icon name="chart" size={21} />
        </span>
        <div>
          <strong>电商运营分析助手</strong>
          <small>经营分析工作台</small>
        </div>
      </div>
      <Button
        type="primary"
        block
        icon={<Icon name="plus" />}
        className="new-analysis"
        onClick={() => {
          setSearch("");
          onSelect(null);
        }}
      >
        新建分析
      </Button>
      <div className="history-heading">
        <h2>分析记录</h2>
        <span>{tasks.length}</span>
        <button
          className="mobile-history-toggle"
          type="button"
          aria-expanded={historyOpen}
          aria-controls="analysis-history"
          aria-label={historyOpen ? "收起分析记录" : "展开分析记录"}
          onClick={() => setHistoryOpen((value) => !value)}
        >
          {historyOpen ? "收起 −" : "展开 +"}
        </button>
      </div>
      <Input
        className="history-search"
        aria-label="搜索分析记录"
        placeholder="搜索记录"
        prefix={<Icon name="search" size={15} />}
        value={search}
        onChange={(event) => setSearch(event.target.value)}
        allowClear
      />
      <nav aria-label="研究记录" id="analysis-history">
        {filtered.map((task) => (
          <button
            type="button"
            key={task.id}
            className={`task-link ${selectedId === task.id ? "selected" : ""}`}
            aria-current={selectedId === task.id ? "page" : undefined}
            onClick={() => onSelect(task.id)}
          >
            <Icon name="file" size={16} />
            <span className="task-link-content">
              <strong>{task.query}</strong>
              <small>
                <span className={`status-dot status-${task.status}`} />
                {statusLabels[task.status] || task.status}
                <time>
                  {new Date(task.created_at).toLocaleDateString("zh-CN", {
                    month: "2-digit",
                    day: "2-digit",
                  })}
                </time>
              </small>
            </span>
          </button>
        ))}
        {filtered.length === 0 && (
          <p className="history-empty">
            {search ? "没有匹配的分析记录" : "暂无分析记录"}
            <small>
              {search ? "试试其他关键词。" : "新建分析后，可在这里继续查看。"}
            </small>
          </p>
        )}
      </nav>
      <div className="sidebar-summary">
        <Icon name="activity" size={15} />
        <span>
          {
            tasks.filter(
              (task) =>
                activeStatuses.has(task.status) &&
                task.status !== "waiting_review",
            ).length
          }{" "}
          项进行中
        </span>
        <span>
          {tasks.filter((task) => task.status === "waiting_review").length}{" "}
          项待复核
        </span>
      </div>
      <div className="account">
        <span className="avatar">{email.slice(0, 1).toUpperCase()}</span>
        <div>
          <strong>当前账户</strong>
          <span title={email}>{email}</span>
        </div>
        <Button
          type="text"
          size="small"
          aria-label="退出登录"
          title="退出登录"
          icon={<Icon name="logout" size={16} />}
          onClick={onSignOut}
        />
      </div>
    </aside>
  );
}
