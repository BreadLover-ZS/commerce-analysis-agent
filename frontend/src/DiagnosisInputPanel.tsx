import { Alert, Button, Checkbox } from "antd";
import { useEffect, useState } from "react";
import type { Dispatch, SetStateAction } from "react";
import { api, apiUrl } from "./lib/api";

export interface DiagnosisInput {
  inventoryId: string | null;
  documentIds: string[];
  comparisonStart: string;
  comparisonEnd: string;
  skus: string;
}
export const emptyDiagnosisInput: DiagnosisInput = {
  inventoryId: null,
  documentIds: [],
  comparisonStart: "",
  comparisonEnd: "",
  skus: "",
};
interface Inventory {
  id: string;
  name: string;
  content_hash: string;
  sample: boolean;
  rows: number;
  snapshot_start: string;
  snapshot_end: string;
  items?: {
    sku: string;
    snapshot_day: string;
    available_units: number;
    lead_days: number | null;
  }[];
}
interface DocumentOption {
  id: string;
  name: string;
  sample?: boolean;
}

export default function DiagnosisInputPanel({
  value,
  onChange,
  ensureConversation,
  onBusy,
}: {
  value: DiagnosisInput;
  onChange: Dispatch<SetStateAction<DiagnosisInput>>;
  ensureConversation: () => Promise<string>;
  onBusy: (busy: boolean) => void;
}) {
  const [inventory, setInventory] = useState<Inventory[]>([]);
  const [preview, setPreview] = useState<Inventory | null>(null);
  const [documents, setDocuments] = useState<DocumentOption[]>([]);
  const [sample, setSample] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const update = (patch: Partial<DiagnosisInput>) =>
    onChange((previous) => ({ ...previous, ...patch }));
  useEffect(() => {
    let disposed = false;
    api<{ datasets: Inventory[] }>("/api/inventory/datasets")
      .then((result) => {
        if (!disposed) setInventory(result.datasets);
      })
      .catch((e) => {
        if (!disposed) setError(e.message);
      });
    return () => {
      disposed = true;
    };
  }, []);
  useEffect(() => {
    onBusy(busy || !!preview);
    return () => onBusy(false);
  }, [busy, preview, onBusy]);
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
  async function loadDocuments() {
    const conversation = await ensureConversation();
    const result = await api<{
      documents: DocumentOption[];
      truncated: boolean;
    }>(`/api/documents?conversation_id=${encodeURIComponent(conversation)}`);
    setDocuments(result.documents);
    if (result.truncated)
      setError("仅展示前200份可见资料，请联系维护人员整理文档。");
  }
  return (
    <section className="panel diagnosis-inputs" aria-label="诊断扩展数据">
      <div className="section-heading">
        <h2>诊断范围与补充资料</h2>
        <span>03 / 04</span>
      </div>
      <p className="field-hint">
        销售数据必须确认；库存和规则可选。每次任务固定本期、对比期与资料版本，缺少的资料会作为缺口保留。
      </p>
      {error && (
        <Alert
          type="error"
          message={error}
          closable
          onClose={() => setError("")}
        />
      )}
      <div className="form-grid date-grid">
        <label>
          对比期开始
          <input
            aria-label="对比期开始"
            type="date"
            value={value.comparisonStart}
            onChange={(e) => update({ comparisonStart: e.target.value })}
          />
        </label>
        <label>
          对比期结束
          <input
            aria-label="对比期结束"
            type="date"
            value={value.comparisonEnd}
            onChange={(e) => update({ comparisonEnd: e.target.value })}
          />
        </label>
      </div>
      <p className="field-hint">
        两项留空时使用等长前一期；自选对比期须等长且早于本期。两期的具体日期会保存在输入组合和报告中。
      </p>
      <label>
        授权商品范围
        <input
          aria-label="授权商品范围"
          placeholder="留空为全部商品，或用逗号分隔SKU"
          value={value.skus}
          onChange={(e) => update({ skus: e.target.value })}
        />
      </label>
      <h3>库存快照</h3>
      <p>
        需要固定快照日期、可用库存件数与可选提前期；支付件数只作为需求代理。
      </p>
      <div className="import-row">
        <Checkbox
          checked={sample}
          onChange={(e) => setSample(e.target.checked)}
        >
          库存导入为模拟数据
        </Checkbox>
        <label>
          导入库存CSV
          <input
            type="file"
            accept=".csv"
            aria-label="导入库存CSV"
            disabled={busy}
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (!file) return;
              void run(async () => {
                const body = new FormData();
                body.append("file", file);
                body.append("sample", String(sample));
                setPreview(
                  await api<Inventory>("/api/inventory/datasets/preview", {
                    method: "POST",
                    body,
                  }),
                );
              });
            }}
          />
        </label>
        <a href={apiUrl("/api/inventory/template")} download>
          下载库存CSV模板
        </a>
      </div>
      {preview && (
        <div className="dataset-preview" aria-label="库存导入预览">
          <h3>确认库存预览</h3>
          <p>
            {preview.name} · {preview.sample ? "模拟数据" : "用户声明真实数据"}{" "}
            · {preview.rows} 个SKU · {preview.snapshot_start} 至{" "}
            {preview.snapshot_end}
          </p>
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>SKU</th>
                  <th>快照日期</th>
                  <th>可用件数</th>
                  <th>提前期（天）</th>
                </tr>
              </thead>
              <tbody>
                {preview.items?.map((row) => (
                  <tr key={row.sku}>
                    <td>{row.sku}</td>
                    <td>{row.snapshot_day}</td>
                    <td>{row.available_units}</td>
                    <td>{row.lead_days ?? "缺失"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Button
            loading={busy}
            onClick={() =>
              void run(async () => {
                const result = await api<Inventory>(
                  `/api/inventory/datasets/${preview.id}/commit`,
                  { method: "POST" },
                );
                setInventory((prev) => [
                  result,
                  ...prev.filter((row) => row.id !== result.id),
                ]);
                update({ inventoryId: result.id });
                setPreview(null);
              })
            }
          >
            确认库存版本
          </Button>
          <Button disabled={busy} onClick={() => setPreview(null)}>
            放弃库存预览
          </Button>
        </div>
      )}
      <label>
        已确认的库存版本
        <select
          aria-label="已确认的库存版本"
          value={value.inventoryId || ""}
          disabled={busy}
          onChange={(e) => update({ inventoryId: e.target.value || null })}
        >
          <option value="">不绑定库存</option>
          {inventory.map((row) => (
            <option key={row.id} value={row.id}>
              {row.name} · {row.sample ? "模拟" : "用户声明真实"} ·{" "}
              {row.snapshot_end} · {row.content_hash.slice(0, 8)}
            </option>
          ))}
        </select>
      </label>
      <h3>固定规则资料</h3>
      <p>
        只检索选中文档的固定片段。原文、解读与冲突候选分别显示，解读始终待人工核对。
      </p>
      <div className="import-row">
        <Button disabled={busy} onClick={() => void run(loadDocuments)}>
          选择可见规则资料
        </Button>
        <label>
          上传诊断规则
          <input
            aria-label="上传诊断规则"
            type="file"
            multiple
            accept=".md,.txt,.pdf,.docx,.xlsx"
            disabled={busy}
            onChange={(e) => {
              const files = Array.from(e.target.files || []);
              e.target.value = "";
              if (!files.length) return;
              void run(async () => {
                const body = new FormData();
                body.append("conversation_id", await ensureConversation());
                files.forEach((file) => body.append("files", file));
                const result = await api<{ documents: DocumentOption[] }>(
                  "/api/documents",
                  { method: "POST", body },
                );
                await loadDocuments();
                update({
                  documentIds: [
                    ...new Set([
                      ...value.documentIds,
                      ...result.documents.map((row) => row.id),
                    ]),
                  ],
                });
              });
            }}
          />
        </label>
      </div>
      {documents.length > 0 && (
        <Checkbox.Group
          aria-label="固定规则资料"
          value={value.documentIds}
          disabled={busy}
          onChange={(ids) => update({ documentIds: ids as string[] })}
          options={documents.map((row) => ({
            value: row.id,
            label: `${row.name}${row.sample ? "（模拟资料）" : ""}`,
          }))}
        />
      )}
      <p className="field-hint">
        已选择 {value.documentIds.length}{" "}
        份资料（最多10份）。上传解析不代表语义核验。
      </p>
    </section>
  );
}
