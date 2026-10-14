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
  parameters: { start_date: string; end_date: string; strategy: string };
  report: Report | null;
  validation: {
    passed: boolean;
    semantic_status: string;
    note: string;
    issues: string[];
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
