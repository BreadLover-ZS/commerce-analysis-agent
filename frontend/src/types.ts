export type Source = "sales" | "inventory" | "documents" | "web";
export interface Claim {
  id: string;
  kind: "fact" | "analysis" | "recommendation";
  text: string;
  evidence_ids: string[];
}
export interface Report {
  title: string;
  sections: { name: string; claims: Claim[] }[];
  limitations: string[];
}
export interface DiagnosisStep {
  step_id: string;
  sequence: number;
  status: string;
  proposal: {
    action: string;
    question: string;
    tool_name?: string;
    expected_evidence?: string;
  };
  evidence_ids: string[];
  assessment: {
    hypothesis_status?: string;
    new_information?: boolean;
    error_code?: string;
  } | null;
}
export interface DiagnosisReport {
  profile: "commerce_diagnosis";
  schema_version: number;
  title: string;
  objective: string;
  bundle_id: string;
  bundle_hash: string;
  period: { start: string; end: string };
  comparison_period: { start: string; end: string };
  scope: { skus: string[]; objective: string | null };
  versions: Record<string, string>;
  report_version: number;
  execution_mode: string;
  facts: {
    evidence_id: string;
    metric_key: string;
    entity_id?: string;
    text: string;
    value: string;
    unit: string;
    snapshot_as_of?: string | null;
  }[];
  observations: {
    finding_id: string;
    hypothesis_code: string;
    status: string;
    text: string;
    supporting_evidence: string[];
    contradicting_evidence: string[];
    missing_data: string[];
  }[];
  steps: DiagnosisStep[];
  stop_reason: string;
  open_questions: string[];
  recommendations: string[];
  limitations: string[];
  rule_quotes?: {
    evidence_id: string;
    document_name: string;
    chunk_id: string;
    quote: string;
    quote_hash: string;
  }[];
  rule_interpretations?: {
    text: string;
    status: string;
    references: { evidence_id: string; chunk_id: string; quote_hash: string }[];
  }[];
  rule_conflicts?: { evidence_id: string; status: string; reason: string }[];
}
export interface DiagnosisProjection {
  task_id: string;
  state_version: number;
  bundle_id: string;
  report_hash: string | null;
  report_version: number;
  steps: DiagnosisStep[];
  pending_input: {
    interrupt_id: string;
    type: string;
    expected_version: number;
    waiting_until: string;
    payload: {
      question?: string;
      options?: string[];
      report_hash?: string;
      report_version?: number;
    };
  } | null;
}
export interface Evidence {
  id: string;
  kind: string;
  title: string;
  locator: string;
  excerpt: string;
  content_hash: string;
  retrieved_at: string;
  payload: { sample?: boolean; [key: string]: unknown };
}
export interface TaskEvent {
  id: number;
  kind: string;
  message: string;
  timestamp: string;
  data: Record<string, unknown>;
}
export interface Task {
  id: string;
  conversation_id: string;
  query: string;
  status: string;
  mode: string;
  created_at: string;
  error_code: string | null;
  parameters: {
    start_date: string;
    end_date: string;
    strategy: string;
    profile?: string;
    dataset_id?: string | null;
    bundle_id?: string | null;
    sources?: Source[];
    require_review?: boolean;
    budget?: Record<string, number>;
  };
  state_version?: number;
  report_hash?: string | null;
  report: Report | DiagnosisReport | null;
  validation: {
    passed: boolean;
    semantic_status: string;
    note: string;
    issues: string[];
    research_validation?: {
      passed: boolean;
      semantic_status: string;
    };
  } | null;
  metrics: {
    tool_calls: number;
    model_calls: number;
    runtime_ms: number;
    input_tokens: number;
    output_tokens: number;
    usage_complete: boolean;
    estimated_cost?: number | null;
  };
  artifacts?: { id: string; name: string; size: number; sha256: string }[];
}
export interface User {
  email: string;
  csrf_token: string;
}
export interface AppConfig {
  mode: string;
  demo_accounts: boolean;
  knowledge_provider: string;
  sample_period: string[];
}
