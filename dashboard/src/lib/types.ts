import type { McpClient } from "./format";

export type Provider = {
  name: string;
  label: string;
  kind: string;
  configured: boolean;
  paid: boolean;
  base_url: string;
  probe?: { reachable: boolean; latency_ms?: number; model_count?: number; error?: string };
};

export type System = {
  gateway: { version: string; python: string; home: string; bind: string; database: string; permissions: Record<string, boolean | number> };
  database: { ok: boolean };
  desktop: { platform: string; installed: boolean; locations: string[] };
  mcp_clients: McpClient[];
  providers: Provider[];
  executions: Record<string, number>;
  open_session_tasks: number;
  model_calls_24h: { total: number; errors: number };
};

export type Execution = {
  execution_id: string;
  kind: string;
  status: string;
  progress: number;
  message: string;
  error: string | null;
  result: Record<string, unknown> | null;
  params: Record<string, unknown>;
  actor: string;
  created_at: string;
  updated_at: string;
  terminal: boolean;
};

export type ScoreSummary = { mean: number; pass_rate: number; n: number };
export type RunSummary = {
  items: number;
  errors: number;
  scores: Record<string, ScoreSummary>;
  latency_ms: { p50: number; p95: number; mean: number } | null;
  tokens: { input: number; output: number };
  cost_usd: number | null;
  agent?: {
    completion_rate: number;
    success_rate: number;
    partial_score: number;
    avg_steps: number;
    avg_tool_calls: number;
    tool_success_rate: number | null;
    failure_recovery_rate: number | null;
    model_calls: number;
    timeouts: number;
    errors: number;
  } | null;
};

export type Run = {
  run_id: string;
  execution_id: string;
  status: string;
  target: { provider: string; model?: string | null };
  experiment_id: string | null;
  summary: RunSummary | null;
  created_at: string;
};

export type Task = {
  task_id: string;
  execution_id: string;
  kind: string;
  status: string;
  payload: Record<string, unknown>;
  created_at: string;
};
