// Data returned by the Python backend (src/reposense/server.py) and the messages exchanged with
// the webviews. Field names match the Python dicts exactly.

// ---------------------------------------------------------------- backend data

export interface MapNode {
  id: string;
  name: string;
  path: string;
  kind: 'repo' | 'layer' | 'dir' | 'file';
  layer: string | null;
  layer_kind?: string;
  description?: string | null;
  lines: number;
  files: number;
  churn: number;
  language?: string | null;
  languages?: Record<string, number>;
  children?: MapNode[];
}

export interface RepoStructure {
  repo: string;
  head: string;
  summary: string;
  root: MapNode;
}

/** `source` imports `target`; `weight` counts the import statements and references. */
export interface DependencyEdge {
  source: string;
  target: string;
  weight: number;
}

export interface Dependencies {
  head: string;
  parsed: number;
  edges: DependencyEdge[];
}

export interface FileCommit {
  sha: string;
  short: string;
  author: string;
  email: string;
  date: string;
  subject: string;
  additions: number;
  deletions: number;
  pr: { number: number; url: string } | null;
  url: string;
  ranges: [number, number][];
  owned_lines: number;
  index: number;
}

export interface FileHistory {
  path: string;
  total_lines: number;
  limit: number;
  commits: FileCommit[];
  older_lines: number;
}

export interface PrDetail {
  number: number;
  title: string;
  url: string;
  author: string;
  state: string;
  merged_at: string;
  body: string;
  reviews: { reviewer: string; state: string; body: string }[];
  review_comments: { reviewer: string; path: string; line: number | null; body: string }[];
  files: string[];
}

export interface CommitDetail {
  sha: string;
  path: string;
  subject: string;
  body: string;
  author: string;
  date: string;
  url: string;
  diff: string;
  pr: PrDetail | null;
  pr_error?: string;
}

export interface FileInsight {
  path: string;
  summary: string;
  role: string;
  responsibilities: string[];
  symbols: { name: string; kind: string; line: number; description: string }[];
  collaborators: { path: string; relation: string }[];
  newcomer_tips: string[];
  dropped: string[];
}

export interface IssueRef {
  number: number;
  title: string;
  state: string;
  created_at: string;
  url: string;
  labels: string[];
  comments: number;
  match: string;
}

export interface Suggestion {
  title: string;
  kind: string;
  difficulty: 'easy' | 'medium' | 'hard';
  start_line: number;
  end_line: number;
  anchor: string;
  detail: string;
}

export interface Opportunities {
  path: string;
  issues: IssueRef[];
  suggestions: Suggestion[];
  dropped: string[];
}

export interface PlanChange {
  file: string;
  start_line: number;
  end_line: number;
  anchor: string;
  what: string;
  why: string;
  confidence: 'high' | 'medium' | 'low';
}

export interface PlanResult {
  repo: string;
  issue: { number: number; title: string; url: string; labels: string[] };
  status: 'ok' | 'incomplete';
  plan: {
    summary: string;
    changes: PlanChange[];
    tests?: { file?: string; command?: string; why: string }[];
    related_prs?: { number: number; title: string; url: string; relevance: string; review_notes?: string[] }[];
    reviewers?: { login: string; why: string }[];
    conventions?: string[];
    open_questions?: string[];
    dropped?: string[];
  } | null;
  run: { steps: number; tool_calls: number; tokens: number; seconds: number; stop_reason: string };
}

export interface PlanEvent {
  type: 'start' | 'thought' | 'tool_call' | 'tool_result' | 'plan_rejected' | 'result' | 'error';
  step?: number;
  name?: string;
  args?: Record<string, unknown>;
  reason?: string;
  summary?: string;
  ok?: boolean;
  errors?: string[];
}

// ---------------------------------------------------------------- webview messages

export type Load<T> = { state: 'idle' } | { state: 'loading'; message?: string } | { state: 'ready'; data: T } | { state: 'error'; message: string };

/** Map panel: host → webview */
export type ToMapView =
  | { type: 'structure'; value: Load<RepoStructure> }
  | { type: 'dependencies'; value: Load<Dependencies> }
  | { type: 'activeFile'; path: string | null };

/** Map panel: webview → host */
export type FromMapView =
  | { type: 'ready' }
  | { type: 'openFile'; path: string }
  | { type: 'refresh' };

/** File Insights view: host → webview */
export type ToFileView =
  | { type: 'file'; path: string | null; repo: string | null }
  | { type: 'history'; value: Load<FileHistory> }
  | { type: 'detail'; sha: string; value: Load<CommitDetail> }
  | { type: 'insight'; value: Load<FileInsight> }
  | { type: 'opportunities'; value: Load<Opportunities> }
  | { type: 'plan'; number: number; value: Load<PlanResult>; events: PlanEvent[] }
  | { type: 'focus'; sha: string | null };

/** File Insights view: webview → host */
export type FromFileView =
  | { type: 'ready' }
  | { type: 'focusCommit'; sha: string | null }
  | { type: 'loadDetail'; sha: string }
  | { type: 'loadInsight'; refresh?: boolean }
  | { type: 'loadOpportunities'; refresh?: boolean }
  | { type: 'planIssue'; number: number }
  | { type: 'reveal'; path: string; line: number; endLine?: number }
  | { type: 'openExternal'; url: string }
  | { type: 'openMap' };
