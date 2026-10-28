// Mirrors the JSON produced by `python -m reposense plan --jsonl` (src/reposense/agent.py).

export interface Change {
  file: string;
  start_line: number;
  end_line: number;
  anchor: string;
  what: string;
  why: string;
  confidence: 'high' | 'medium' | 'low';
}

export interface RelatedPr {
  number: number;
  title: string;
  url: string;
  author: string;
  merged_at: string;
  relevance: string;
  review_notes?: string[];
}

export interface Plan {
  summary: string;
  changes: Change[];
  tests?: { file?: string; command?: string; why: string }[];
  related_prs?: RelatedPr[];
  reviewers?: { login: string; why: string }[];
  conventions?: string[];
  open_questions?: string[];
  dropped?: string[];
}

export interface BlameBlock {
  start: number;
  end: number;
  sha: string;
  author?: string;
  date?: string;
  summary?: string;
  pr?: number | null;
  pr_title?: string | null;
  pr_url?: string | null;
}

export interface Issue {
  number: number;
  title: string;
  url: string;
  labels: string[];
}

export interface Result {
  repo: string;
  commit: string;
  issue: Issue;
  status: 'ok' | 'incomplete';
  plan: Plan | null;
  history: Record<string, BlameBlock[]>;
  run: {
    model: string;
    use_history: boolean;
    steps: number;
    tool_calls: number;
    tokens: number;
    seconds: number;
    stop_reason: string;
    plan_rejections: number;
  };
}

export type AgentEvent =
  | { type: 'start'; repo: string; issue: Issue & { body?: string }; model: string; use_history: boolean }
  | { type: 'thought'; step: number; text: string }
  | { type: 'tool_call'; step: number; name: string; args: Record<string, unknown>; reason: string }
  | { type: 'tool_result'; step: number; name: string; summary: string; ok: boolean }
  | { type: 'plan_rejected'; step: number; errors: string[] }
  | { type: 'result'; result: Result }
  | { type: 'error'; message: string };
