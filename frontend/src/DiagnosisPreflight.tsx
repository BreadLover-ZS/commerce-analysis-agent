import { Alert, Checkbox } from "antd";

type Coverage = {
  expected_records: number;
  observed_records: number;
  missing_records: number;
  complete: boolean;
};
export interface InputQuality {
  bundle_id: string;
  bundle_hash: string;
  period: { start: string; end: string };
  comparison_period: { start: string; end: string };
  allowed_skus: string[];
  sample: boolean;
  source_samples: Record<string, boolean>;
  coverage: { current: Coverage; comparison: Coverage };
  comparable: boolean;
  supported_metrics: string[];
  unsupported_metrics: string[];
  inventory: {
    items: {
      sku: string;
      status: string;
      reason: string | null;
      snapshot_day: string | null;
    }[];
  } | null;
  documents: { count: number; chunks: number; semantic_status: string } | null;
  limitations: string[];
}
const labels: Record<string, string> = {
  net_revenue: "净销售额",
  gross_revenue: "支付金额",
  refund_amount: "退款金额",
  paid_units: "支付件数",
  refunded_units: "退款件数",
  profit: "利润",
  conversion_rate: "转化率",
  roi: "广告ROI",
  cohort_refund_rate: "同批订单最终退款率",
  sales: "销售",
  inventory: "库存",
  documents: "规则资料",
  missing_inventory: "缺少库存",
  missing_lead_days: "缺少提前期",
  stale_snapshot: "快照过旧",
  future_snapshot: "快照晚于窗口",
  incomplete_sales_window: "销售窗口不完整",
  zero_demand: "零支付需求，代理不可计算",
};
export default function DiagnosisPreflight({
  quality,
  confirmed,
  onConfirm,
}: {
  quality: InputQuality;
  confirmed: boolean;
  onConfirm: (value: boolean) => void;
}) {
  return (
    <section className="dataset-preview" aria-label="诊断输入核对">
      <h3>确认本次固定输入</h3>
      <p>
        本期：{quality.period.start} 至 {quality.period.end}；对比期：
        {quality.comparison_period.start} 至 {quality.comparison_period.end}
      </p>
      <p>授权商品：{quality.allowed_skus.join("、")}</p>
      <p>
        资料性质：
        {Object.entries(quality.source_samples)
          .map(
            ([source, sample]) =>
              `${labels[source] || source}（${sample ? "模拟" : "用户声明真实"}）`,
          )
          .join("、")}
      </p>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>周期</th>
              <th>应有记录</th>
              <th>已记录</th>
              <th>缺行</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(quality.coverage).map(([name, row]) => (
              <tr key={name}>
                <td>{name === "current" ? "本期" : "对比期"}</td>
                <td>{row.expected_records}</td>
                <td>{row.observed_records}</td>
                <td>{row.missing_records}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!quality.comparable && (
        <Alert
          type="warning"
          message="两期不可完整比较；不计算完整变化归因，诊断将保留缺口。"
        />
      )}
      <p>
        支持的记账指标：
        {quality.supported_metrics.map((key) => labels[key] || key).join("、")}
      </p>
      <p>
        不支持：
        {quality.unsupported_metrics
          .map((key) => labels[key] || key)
          .join("、")}
        。规则原文不证明经营原因。
      </p>
      {quality.inventory && (
        <details open>
          <summary>库存资料覆盖</summary>
          <ul>
            {quality.inventory.items.map((row) => (
              <li key={row.sku}>
                {row.sku}：
                {row.status === "ok"
                  ? "可按固定窗口核对代理"
                  : labels[row.reason || ""] || row.reason}
                ；快照：{row.snapshot_day || "缺失"}
              </li>
            ))}
          </ul>
        </details>
      )}
      {quality.documents && (
        <p>
          固定规则资料：{quality.documents.count} 份 /{" "}
          {quality.documents.chunks} 个片段；解读与适用性未核验。
        </p>
      )}
      <details>
        <summary>口径与输入校验</summary>
        <ul>
          {quality.limitations.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
        <p className="hash-value">输入哈希：{quality.bundle_hash}</p>
      </details>
      <Checkbox
        checked={confirmed}
        onChange={(event) => onConfirm(event.target.checked)}
      >
        我已核对以上周期、范围和资料缺口
      </Checkbox>
    </section>
  );
}
