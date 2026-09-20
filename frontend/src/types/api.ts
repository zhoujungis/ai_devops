/**
 * Types mirroring the backend's API shapes.
 *
 * Kept in one file deliberately: these are the contract between two codebases, and
 * scattering them makes drift invisible. When the backend changes, this file is the
 * only place that should need to change.
 */

export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    details: Record<string, unknown>;
    request_id: string;
  };
}

export interface Paginated<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

export interface User {
  id: string;
  email: string;
  display_name: string;
  avatar_url: string;
  date_joined: string;
  is_active: boolean;
}

export interface Organization {
  id: string;
  name: string;
  slug: string;
  plan: string;
  created_at: string;
  role: string | null;
}

export interface Project {
  id: string;
  org: string;
  name: string;
  slug: string;
  description: string;
  status: string;
  created_at: string;
  role: string | null;
}

// ---------------------------------------------------------------------------
// code
// ---------------------------------------------------------------------------
export interface ModuleRef {
  id: string;
  name: string;
  kind: string;
  path_prefix: string;
}

export interface ModuleImpact {
  module: ModuleRef;
  churn_lines: number;
  file_count: number;
  weight: number;
  is_test_change: boolean;
}

export interface Commit {
  id: string;
  repository: string;
  sha: string;
  short_sha: string;
  message: string;
  author_name: string;
  author_email: string;
  committed_at: string;
  additions: number;
  deletions: number;
  files_changed: number;
  module_impacts: ModuleImpact[];
  created_at: string;
}

export interface Module {
  id: string;
  name: string;
  kind: string;
  path_prefix: string;
  language: string;
  centrality_score: number;
  owner_team: string;
  created_at: string;
}

// ---------------------------------------------------------------------------
// risk
// ---------------------------------------------------------------------------
export interface RiskContribution {
  signal: string;
  label: string;
  raw: number;
  normalized: number;
  weight: number;
  contribution: number;
  detail: string;
}

export interface RiskAssessment {
  score: number;
  level: "low" | "medium" | "high" | "critical";
  breakdown: RiskContribution[];
}

// ---------------------------------------------------------------------------
// AI
// ---------------------------------------------------------------------------
export interface TokenUsage {
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  cost_usd: number | null;
  runs: number;
}

export interface AIJob {
  id: string;
  project: string;
  agent_code: string;
  target_type: string;
  target_id: string;
  params: Record<string, unknown>;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  progress: number;
  error: string;
  result: Record<string, unknown> | null;
  usage: TokenUsage;
  findings: string[];
  created_at: string;
}

export interface EvidenceRef {
  kind: string;
  ref_id: string;
  note: string;
}

export interface AIFinding {
  id: string;
  project: string;
  job: string | null;
  agent_code: string;
  severity: string;
  category: string;
  title: string;
  summary: string;
  payload: Record<string, unknown>;
  confidence: number;
  evidence: EvidenceRef[];
  status: string;
  created_at: string;
}

export interface RecommendationConfirmation {
  decision: "confirmed" | "rejected";
  reason: string;
  executor_code: string;
  result: Record<string, unknown>;
  executed_at: string | null;
}

export interface AIRecommendation {
  id: string;
  project: string;
  agent_code: string;
  type: string;
  title: string;
  description: string;
  payload: Record<string, unknown>;
  risk_level: string;
  status: "pending" | "executed" | "rejected" | "failed" | "expired";
  created_at: string;
  confirmation: RecommendationConfirmation | null;
}

// ---------------------------------------------------------------------------
// explain
// ---------------------------------------------------------------------------
export interface ExplainedModule {
  module: ModuleRef;
  weight: number;
  churn_lines: number;
  file_count: number;
  is_test_change: boolean;
}

export interface RegressionCandidate {
  key: string;
  title: string;
  priority: string;
  score: number;
  reasons: string[];
}

export interface HistoricalBug {
  key: string;
  title: string;
  severity: string;
  status: string;
  shared_modules: string[];
}

export interface CommitExplanation {
  commit: {
    id: string;
    repository: string;
    sha: string;
    short_sha: string;
    message: string;
    committed_at: string;
  };
  modules: ExplainedModule[];
  regression_candidates: RegressionCandidate[];
  historical_bugs: HistoricalBug[];
  requirement: { id: string; external_key: string; title: string; status: string; priority: string } | null;
  requirement_source: string;
  releases: { id: string; version: string; name: string; status: string; released_at: string | null }[];
  data_gaps: string[];
}
