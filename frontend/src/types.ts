// 与后端 Pydantic 模型对应的类型（只列出前端用到的字段）

export type Unit = "CNY" | "ratio" | "times" | "days" | "score" | "cny_per_share" | "count" | "text" | "bool";
export type Severity = "high" | "medium" | "low" | "info";

export interface SourceRef {
  provider: string;
  dataset: string;
  snapshot_id: string;
  sha256: string;
  url: string;
  fetched_at: string;
  field?: string | null;
  report_date?: string | null;
  notice_date?: string | null;
}

export interface Evidence {
  id: string;
  kind: "fact" | "metric" | "check" | "signal" | "model" | "text" | "event" | "param";
  label: string;
  value: number | string | boolean | null;
  unit: Unit;
  period?: string | null;
  formula?: string | null;
  expression?: string | null;
  inputs: string[];
  source?: SourceRef | null;
  note?: string | null;
  status: "ok" | "missing" | "not_applicable" | "error";
}

export interface Period {
  end: string;
  type: string;
  label: string;
  fiscal_year: number;
  notice_date?: string | null;
  audit_opinion?: string | null;
}

export interface CompanyProfile {
  code: string;
  exchange: string;
  name: string;
  full_name?: string | null;
  company_type: string;
  industry_em?: string | null;
  industry_csrc?: string | null;
  listing_date?: string | null;
  auditor?: string | null;
  province?: string | null;
  profile?: string | null;
}

export interface MetricRow {
  code: string;
  label: string;
  unit: Unit;
  dimension: string;
  formula: string;
  description: string;
  higher_is_better: boolean | null;
  values: Record<string, string | null>;
}

export interface Signal {
  rule: string;
  title: string;
  dimension: string;
  severity: Severity;
  polarity: "risk" | "strength";
  period?: string | null;
  summary: string;
  evidence: string[];
  rationale: string;
  threshold_type: "literature" | "heuristic" | "fact";
  reference?: string | null;
}

export interface CheckResult {
  code: string;
  title: string;
  category: "identity" | "continuity" | "vendor" | "balance";
  period: string;
  status: "pass" | "fail" | "skip";
  lhs?: number | null;
  rhs?: number | null;
  diff?: number | null;
  severity: "error" | "warning" | "info";
  evidence_id: string;
  note?: string | null;
}

export interface DimensionSummary {
  code: string;
  label: string;
  level: "high" | "medium" | "low" | "info" | "none";
  risk_count: number;
  strength_count: number;
}

export interface ModelBlock {
  label: string;
  formula: string;
  description: string;
  reference: string;
  values: Record<string, string | null>;
  components: { code: string; label: string; formula: string; values: Record<string, string> }[];
}

export interface Announcement {
  id: string;
  title: string;
  date: string | null;
  type?: string | null;
  pdf_url?: string | null;
}

export interface DiagnosisResult {
  engine_version: string;
  generated_at: string;
  profile: CompanyProfile;
  periods: Period[];
  metrics: MetricRow[];
  models: Record<string, ModelBlock>;
  checks: CheckResult[];
  checks_summary: { pass: number; fail: number; skip: number; error_fail: number };
  signals: Signal[];
  dimensions: DimensionSummary[];
  overall: { level: string; level_code: string; headline: string; reasons: string[]; rule: string };
  statements: Record<string, Record<string, Record<string, string>>>;
  evidence: Record<string, Evidence>;
  announcements: Announcement[];
  data_notes: string[];
}

export interface Claim {
  text: string;
  start: number;
  end: number;
  refs: string[];
  evidence_id?: string | null;
  status: "verified" | "unattributed" | "mismatch" | "invalid_ref" | "unverified";
  expected?: string | null;
  message: string;
  sentence: string;
}

export interface Verification {
  claims: Claim[];
  total: number;
  verified: number;
  unattributed: number;
  mismatch: number;
  invalid_ref: number;
  unverified: number;
  verifiable_rate: number;
  citation_rate: number;
  compliance_flags: string[];
}

export interface Report {
  code: string;
  name: string;
  mode: "llm" | "template";
  model?: string | null;
  markdown: string;
  refs: Record<string, string>;
  verification: Verification;
  attempts: number;
  revised: boolean;
  usage: Record<string, number>;
  warnings: string[];
  generated_at: string;
  elapsed_ms: number;
}

export interface AgentStep {
  tool: string;
  arguments: Record<string, unknown>;
  summary: string;
  refs: string[];
  latency_ms: number;
  error?: string | null;
}

export interface AgentAnswer {
  question: string;
  answer: string;
  refs: Record<string, string>;
  evidence: Record<string, Evidence>;
  verification: Verification;
  steps: AgentStep[];
  attempts: number;
  model?: string | null;
  usage: Record<string, number>;
  warnings: string[];
  elapsed_ms: number;
  ok?: boolean;
}

export interface EvidenceDetail {
  evidence: Evidence;
  chain: Evidence[];
  snapshots: Record<string, { snapshot_id: string; provider: string; dataset: string; url: string; fetched_at: string; sha256: string; size: number }>;
}

export interface CompanyBrief {
  code: string;
  name: string;
  industry?: string | null;
  company_type: string;
}
