// Typed facade over the backend RPC methods. Features call this, never RpcClient directly.
import * as vscode from 'vscode';
import {
  CommitDetail, FileHistory, FileInsight, Opportunities, PlanEvent, PlanResult, RepoStructure,
} from '../shared/protocol';
import { RpcClient } from './rpcClient';

export const SECRET_GITHUB = 'reposense.githubToken';
export const SECRET_DEEPSEEK = 'reposense.deepseekKey';

type Listener<T> = (value: T) => void;

export class Backend implements vscode.Disposable {
  private readonly rpc: RpcClient;
  private readonly progressListeners = new Set<Listener<{ task: string; message: string; percent?: number }>>();
  private readonly planListeners = new Set<Listener<PlanEvent>>();
  private openedRoot?: string;
  private keys: { github?: string; deepseek?: string } = {};
  readonly output = vscode.window.createOutputChannel('RepoSense');

  constructor(private readonly secrets: vscode.SecretStorage) {
    this.rpc = new RpcClient(
      () => {
        const cfg = vscode.workspace.getConfiguration('reposense');
        const env: NodeJS.ProcessEnv = {};
        if (this.keys.github) env.GITHUB_TOKEN = this.keys.github;
        if (this.keys.deepseek) env.DEEPSEEK_API_KEY = this.keys.deepseek;
        return {
          python: cfg.get<string>('pythonPath', 'python'),
          cwd: vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? process.cwd(),
          env,
        };
      },
      (method, params) => {
        if (method === 'progress') this.progressListeners.forEach((l) => l(params));
        if (method === 'plan.event') this.planListeners.forEach((l) => l(params.event));
      },
      (line) => this.output.appendLine(line),
    );
  }

  async loadKeys(): Promise<void> {
    this.keys = {
      github: await this.secrets.get(SECRET_GITHUB),
      deepseek: await this.secrets.get(SECRET_DEEPSEEK),
    };
  }

  hasKeys(): boolean {
    return !!(this.keys.github && this.keys.deepseek);
  }

  async restart(): Promise<void> {
    await this.loadKeys();
    this.rpc.stop();
    this.openedRoot = undefined;
  }

  onProgress(l: Listener<{ task: string; message: string; percent?: number }>): vscode.Disposable {
    this.progressListeners.add(l);
    return new vscode.Disposable(() => this.progressListeners.delete(l));
  }

  onPlanEvent(l: Listener<PlanEvent>): vscode.Disposable {
    this.planListeners.add(l);
    return new vscode.Disposable(() => this.planListeners.delete(l));
  }

  // ------------------------------------------------------------- methods

  checkConfig(): Promise<{ missing: string[]; model: string }> {
    return this.rpc.request('config.check');
  }

  clone(url: string, parentDir: string): Promise<{ repo: string; root: string }> {
    return this.rpc.request('repo.clone', { url, parent_dir: parentDir });
  }

  /** Opens the workspace repository once per backend process. */
  async ensureRepo(root: string): Promise<{ repo: string; root: string; head: string }> {
    const lang = vscode.workspace.getConfiguration('reposense').get<string>('language', 'Chinese');
    const info = await this.rpc.request<{ repo: string; root: string; head: string }>('repo.open', { path: root, lang });
    this.openedRoot = root;
    return info;
  }

  structure(refresh = false): Promise<RepoStructure> {
    return this.rpc.request('repo.structure', { refresh });
  }

  fileHistory(path: string, limit: number): Promise<FileHistory> {
    return this.rpc.request('file.history', { path, limit });
  }

  commitDetail(sha: string, path: string): Promise<CommitDetail> {
    return this.rpc.request('commit.detail', { sha, path });
  }

  fileInsight(path: string, refresh = false): Promise<FileInsight> {
    return this.rpc.request('file.insight', { path, refresh });
  }

  opportunities(path: string, symbols: string[], refresh = false): Promise<Opportunities> {
    return this.rpc.request('file.opportunities', { path, symbols, refresh });
  }

  issuePlan(number: number): Promise<PlanResult> {
    return this.rpc.request('issue.plan', { number });
  }

  get repoRoot(): string | undefined {
    return this.openedRoot;
  }

  dispose(): void {
    this.rpc.stop();
    this.output.dispose();
  }
}
