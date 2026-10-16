import { Alert, Button, Checkbox } from "antd";
import { useEffect, useState } from "react";
import { api, apiUrl } from "./lib/api";

export interface Dataset {
  id: string;
  name: string;
  sample: boolean;
  committed: boolean;
  content_hash: string;
  rows: number;
  products: number;
  suggested_start: string;
  period_end: string;
  quality?: Quality;
}
interface Quality {
  coverage: {
    expected_records: number;
    observed_records: number;
    missing_records: number;
    complete: boolean;
  };
  previous_period: { comparable: boolean };
  limitations: string[];
  metrics: Record<string, string>;
}
interface Props {
  selected: string | null;
  start: string;
  end: string;
  onSelect: (dataset: Dataset) => void;
  onChecked: (signature: string | null) => void;
}
const scenarios = [
  ["normal", "正常经营"],
  ["sales_decline", "销售下降"],
  ["refund_spike", "退款增加"],
  ["top_decline", "头部商品下滑"],
  ["missing", "缺失记录"],
  ["zero", "零销量"],
  ["no_prior", "前期不可比较"],
  ["new_product", "新品上架"],
  ["cross_refund", "跨期退款"],
];

export default function CommerceDataPanel({
  selected,
  start,
  end,
  onSelect,
  onChecked,
}: Props) {
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [preview, setPreview] = useState<Dataset | null>(null);
  const [scenario, setScenario] = useState("normal");
  const [sample, setSample] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [quality, setQuality] = useState<Quality | null>(null);
  useEffect(() => {
    let disposed = false;
    api<{ datasets: Dataset[] }>("/api/commerce/datasets")
      .then((result) => {
        if (!disposed) setDatasets(result.datasets);
      })
      .catch((e) => {
        if (!disposed) setError(e.message);
      });
    return () => {
      disposed = true;
    };
  }, []);
  useEffect(() => {
    let disposed = false;
    onChecked(null);
    setQuality(null);
    if (selected && start && end) {
      api<Quality>(
        `/api/commerce/datasets/${selected}/quality?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`,
      )
        .then((result) => {
          if (!disposed) {
            setQuality(result);
            onChecked(`${selected}:${start}:${end}`);
          }
        })
        .catch((e) => {
          if (!disposed) setError(e.message);
        });
    }
    return () => {
      disposed = true;
    };
  }, [selected, start, end, onChecked]);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function confirm() {
    if (!preview) return;
    const dataset = await api<Dataset>(
      `/api/commerce/datasets/${preview.id}/commit`,
      { method: "POST" },
    );
    setDatasets((prev) => [
      dataset,
      ...prev.filter((d) => d.id !== dataset.id),
    ]);
    onSelect(dataset);
    setPreview(null);
  }
  return (
    <section className="panel commerce-data" aria-label="经营数据管理">
      <div className="section-heading">
        <div>
          <span className="section-kicker">数据准备</span>
          <h2>经营数据</h2>
        </div>
        <span>02 / 03</span>
      </div>
      <p className="field-hint">
        生成模拟场景或导入 CSV，预览后确认使用。每次复盘绑定固定数据版本。
      </p>
      {error && (
        <Alert
          type="error"
          message={error}
          closable
          onClose={() => setError("")}
        />
      )}
      <div className="data-source-actions">
        <label>
          模拟场景
          <select
            aria-label="模拟场景"
            value={scenario}
            onChange={(e) => setScenario(e.target.value)}
          >
            {scenarios.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <Button
          loading={busy}
          onClick={() =>
            void run(async () => {
              setPreview(
                await api<Dataset>("/api/commerce/scenarios", {
                  method: "POST",
                  body: JSON.stringify({ scenario, seed: 42 }),
                }),
              );
            })
          }
        >
          生成模拟数据
        </Button>
        <a href={apiUrl("/api/commerce/template")} download>
          下载模拟CSV模板
        </a>
      </div>
      <div className="import-row">
        <Checkbox
          checked={sample}
          onChange={(e) => setSample(e.target.checked)}
        >
          导入内容为模拟数据
        </Checkbox>
        <label>
          导入经营CSV
          <input
            aria-label="导入经营CSV"
            type="file"
            accept=".csv"
            disabled={busy}
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (!file) return;
              setPreview(null);
              void run(async () => {
                const data = new FormData();
                data.append("file", file);
                data.append("sample", String(sample));
                setPreview(
                  await api<Dataset>("/api/commerce/datasets/preview", {
                    method: "POST",
                    body: data,
                  }),
                );
              });
            }}
          />
        </label>
      </div>
      {!sample && (
        <p>请确认上传内容确为真实数据。模板和生成场景都属于模拟数据。</p>
      )}
      {preview && (
        <div className="dataset-preview" aria-label="导入预览">
          <div className="section-heading">
            <h3>确认数据预览</h3>
            <span>尚未提交</span>
          </div>
          <table className="data-table">
            <tbody>
              <tr>
                <th scope="row">数据名称</th>
                <td>{preview.name}</td>
              </tr>
              <tr>
                <th scope="row">数据性质</th>
                <td>{preview.sample ? "模拟数据" : "用户声明的真实数据"}</td>
              </tr>
              <tr>
                <th scope="row">数据规模</th>
                <td>
                  {preview.products} 个商品 / {preview.rows} 条记录
                </td>
              </tr>
              <tr>
                <th scope="row">默认分析周期</th>
                <td>
                  {preview.suggested_start} 至 {preview.period_end}
                </td>
              </tr>
              <tr>
                <th scope="row">版本校验</th>
                <td>{preview.content_hash.slice(0, 12)}</td>
              </tr>
            </tbody>
          </table>
          {preview.quality?.limitations.map((note) => (
            <p key={note}>{note}</p>
          ))}
          <Button
            type="primary"
            loading={busy}
            onClick={() => void run(confirm)}
          >
            确认使用此数据版本
          </Button>
          <Button disabled={busy} onClick={() => setPreview(null)}>
            放弃预览
          </Button>
        </div>
      )}
      <label className="version-select">
        已确认的数据版本
        <select
          aria-label="已确认的数据版本"
          value={selected || ""}
          onChange={(e) => {
            const dataset = datasets.find((d) => d.id === e.target.value);
            if (dataset) onSelect(dataset);
          }}
        >
          <option value="" disabled>
            请选择或生成一个数据集
          </option>
          {datasets.map((dataset) => (
            <option key={dataset.id} value={dataset.id}>
              {dataset.name} · {dataset.sample ? "模拟" : "用户声明真实"} ·{" "}
              {dataset.content_hash.slice(0, 8)}
            </option>
          ))}
        </select>
      </label>
      {quality && (
        <div className="quality-summary" aria-label="数据质量检查">
          <p>
            当前周期：预期{quality.coverage.expected_records}条，已记录
            {quality.coverage.observed_records}条，缺失
            {quality.coverage.missing_records}条。
          </p>
          <p>
            前期比较：
            {quality.previous_period.comparable
              ? "记录完整，可计算变化金额"
              : "不可用"}
            ；已记录净销售额：{quality.metrics.net_revenue}元。
          </p>
          {quality.limitations.map((note) => (
            <p key={note}>{note}</p>
          ))}
        </div>
      )}
    </section>
  );
}
